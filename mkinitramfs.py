#!/usr/bin/env python
"""
Python initrd generating script
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib


XDG_CONFIG_HOME = os.getenv('XDG_CONFIG_HOME', os.path.expanduser('~/.config'))
XDG_DATA_HOME = os.getenv('XDG_DATA_HOME',
                          os.path.expanduser('~/.local/share'))
CONF_PATH = os.path.join(XDG_CONFIG_HOME, 'mkinitramfs.toml')
KEYS_PATH = os.path.join(XDG_DATA_HOME, 'keys')
SHEBANG = "#!/bin/bash\n"
SHEBANG_ASH = "#!/bin/sh\n"
DEPS = """
DEPS=(
/bin/busybox
/usr/bin/ccrypt
/sbin/cryptsetup
%(lvm)s
%(yubikey)s
)
"""
# /usr/sbin/dropbear
# /usr/bin/dropbearkey
# /usr/sbin/wpa_supplicant
COPY_DEPS = """
for bin in ${DEPS[*]}; do
    cp $bin ./bin/
    ldd $bin >/dev/null || continue
    for lib in $(ldd $bin | sed -nre 's,.* (/.*lib.*/.*.so.*) .*,\\1,p' \\
        -e 's,.*(/lib.*/ld.*.so.*) .*,\\1,p')
    do
        cp $lib lib64/
    done
done
# extra lib for new version of cryptsetup, which need to do locks
for path in $(find /usr/lib/gcc|grep libgcc_s.so.1); do
    [ "$(basename $(dirname $path))" = '32' ] && continue
    cp $path lib/
done
"""
COPY_MODULES = """
KERNEL=$(readlink /usr/src/linux)
VERSION=${KERNEL#linux-}
mkdir -p lib/modules
cp -a "/lib/modules/${VERSION}" lib/modules/
rm -fr lib/modules/misc lib/modules/video
"""
MKCPIO = """
find . -print0 | cpio --quiet --null -o -H newc | \\
    gzip > %(arch)s
exit $?
"""

INIT = """
DEVICE=''
KEYDEV=''
CLEAR=clear

$CLEAR
export PATH=/bin
umask 0077

[ ! -d /proc ] && mkdir /proc
[ ! -d /tmp ] && mkdir /tmp
[ ! -d /mnt ] && mkdir /mnt
[ ! -d /new-root ] && mkdir /new-root

mount -t proc proc /proc
mount -t sysfs sysfs /sys
mount -t devtmpfs devtmpfs /dev

# clean i/o
exec >/dev/console </dev/console 2>&1

# tty fix
mv /dev/tty /dev/tty_bak
ln -s /dev/console /dev/tty

"""

# check for 'rescue' keyword if there should be shell requested
INIT_CMD = """
CMD=`cat /proc/cmdline`

for param in $CMD; do
    if [ "${param}" == "rescue" ]; then
        exec /bin/sh
    elif [ "${param}" == "dbg" ]; then
        set -x
        CLEAR=''
    fi
done
"""

# optional: search for the SD/MMC card, and use it's first partition. The idea
# is to have something which one *own* rather that something that one *know*.
# To prepare SD card (or pendrive, procedure is the same), create partition,
# at least 32MB on dos partition table, format it, write something (possibly
# some images/videoclips), create a key 4096 bytes long, and write it down
# using:
#
# dd if=keyfile of=/dev/mmcblk0p1 seek=31337 count=8
#
# or, for pendrive:
#
# dd if=keyfile of=/dev/sdX1 seek=31337 count=8
#
# be carefull, which disk you select to write.
INIT_SD = """
for counter in $(seq 5); do
    clear
    if [ -b /dev/mmcblk0p1 ]; then
        KEYDEV=/dev/mmcblk0p1
        break
    fi
    sleep 1
done
"""

# optional: search for the labeled device - assuming it will be usb stick with
# one of the partition set with label (e2label, mlabel). for vfat partition
# labels, mlabel have weird format to set it:
#
# mlabel -v -i /dev/sdx1 -s ::foobar
#
# note, that label will always be uppercase, so that case sensitiv check is
# off.
INIT_LABELED = """
for counter in $(seq 3); do
    sleep 1
    clear
    for dev in /dev/sd* /dev/mmcblk*; do
        if blkid "${dev}" | grep -w LABEL | grep -iqw "%(label)s"; then
            KEYDEV="${dev}"
            break
        fi
    done
    [ -n "${KEYDEV}" ] && break
done
"""

# Open encrypted fs
INIT_OPEN = """
for counter in $(seq 3); do
    sleep 1
    $CLEAR
    for dev in /dev/sd* /dev/nvme*; do
        if cryptsetup isLuks ${dev}; then
            if [ $(cryptsetup luksUUID ${dev}) = "${UUID}" ]; then
                DEVICE=$dev
                break
            fi
        fi
    done
    [ -n "${DEVICE}" ] && break
done

if [ -z "${DEVICE}" ]; then
    echo "No LUKS device found to boot from! Giving up."
    sleep 3
    exec reboot -f
fi
"""

DECRYPT_KEYDEV = """
ret=1
if [ -n ${KEYDEV} ]; then
    for i in 0 1 2 ; do
        dd if=${KEYDEV} skip=31337 count=8 2>/dev/null | \
                cryptsetup open --allow-discards $DEVICE root
        ret=$?
        [ ${ret} -eq 0 ] && break
    done
fi

if [[ ${ret} -ne 0 && ! -f ${KEY} ]]; then
    echo "Failed to open boot system fs. Giving up."
    sleep 3
    reboot -f
fi
"""

DECRYPT_YUBICP = """
for i in 1 2 3 4 5 6; do
    pass=$(ykchalresp %(disk)s 2>/dev/null)
    if [ -n "$pass" ]; then
        ccrypt -K $pass -c "$KEY.yk" | \
                cryptsetup open --allow-discards $DEVICE root
        break
    fi
    sleep .5
done

"""

DECRYPT_PASSWORD = """
if [ ! -b /dev/mapper/root ]; then
    for i in 0 1 2 ; do
        ccrypt -c $KEY | cryptsetup open --allow-discards $DEVICE root
        ret=$?
        [ ${ret} -eq 0 ] && break
    done
fi
"""

SWROOT = """
# get the tty back
rm /dev/tty
mv /dev/tty_bak /dev/tty

mount /dev/mapper/root /new-root

# restore hotplug events
echo > /proc/sys/kernel/hotplug

umount -l /proc /sys /dev

exec switch_root /new-root /sbin/init
"""


class Config:
    defaults = {'copy_modules': False,
                'disk_label': None,
                'install': False,
                'key_path': None,
                'lvm': False,
                'no_key': False,
                'sdcard': None,
                'yubikey': False}

    def __init__(self, args, toml_conf):
        self.drive = args.get('drive')
        toml_ = toml_conf[self.drive]

        for k, v in self.defaults.items():
            setattr(self, k, toml_.get(k, v))
            if getattr(self, k) is not args.get(k) and args.get(k) is not None:
                setattr(self, k, args[k])

        key = None
        if not self.key_path and toml_.get('key'):
            key = toml_.get('key')
            if not os.path.exists(key):
                key = os.path.join(KEYS_PATH, key)
            if not os.path.exists(key):
                sys.stderr.write(f'Cannot find key file for '
                                 '{toml_.get("key")}.\n')
                sys.exit(2)
            self.key_path = key

        if not (self.key_path or self.no_key):
            sys.stderr.write(f'key file for {self.drive} is not provided, '
                             'while no-key option is not set.\n')
            sys.exit(2)

        # UUID is only available via config file
        self.uuid = toml_.get('uuid')


class Initramfs(object):
    def __init__(self, conf):
        self.lvm = conf.lvm
        self.yk = conf.yubikey
        self.name = args.disk
        self.modules = conf.copy_modules
        self.key_path = conf.key_path
        self.disk_label = conf.disk_label
        self.sdcard = conf.sdcard
        self.install = conf.install
        self.no_key = conf.no_key

        self.dirname = None
        self.kernel_ver = os.readlink('/usr/src/linux').replace('linux-', '')
        self._make_tmp()
        self._disks = conf.drive

    def _make_tmp(self):
        self.dirname = tempfile.mkdtemp(prefix='init_')
        self.curdir = os.path.abspath(os.curdir)

    def _make_dirs(self):
        os.chdir(self.dirname)
        for dir_ in ('bin', 'dev', 'etc', 'keys', 'lib64', 'proc',
                     'run/cryptsetup', 'run/lock', 'sys', 'tmp'):
            os.makedirs(os.path.join(self.dirname, dir_))

        for link, target in (('lib', 'lib64'), ('sbin', 'bin'),
                             ('linuxrc', 'bin/busybox')):
            os.symlink(target, link)
        os.chdir(self.curdir)

    def _copy_deps(self):
        additional_libs = ['libgcc_s']
        os.chdir(self.dirname)
        _fd, fname = tempfile.mkstemp(dir=self.dirname, suffix='.sh')
        os.close(_fd)
        with open(fname, 'w') as fobj:
            lvm = '/sbin/lvscan\n/sbin/vgchange' if self.lvm else ''
            yubikey = '/usr/bin/ykchalresp' if self.yk else ''
            fobj.write(SHEBANG)
            fobj.write(DEPS % {'lvm': lvm, 'yubikey': yubikey})
            fobj.write(COPY_DEPS)

        # extra crap, which seems to be needed, but is not direct dependency
        for root, _, fnames in os.walk('/usr/lib'):
            if '32' in root:
                continue

            for f in fnames:
                if f.split('.')[0] in additional_libs:
                    shutil.copy(os.path.join(root, f), 'lib64',
                                follow_symlinks=False)

        os.chmod(fname, 0b111101101)
        subprocess.call([fname])
        os.unlink(fname)
        os.chdir(self.curdir)

    def _copy_modules(self):
        if not self.modules:
            return
        os.chdir(self.dirname)
        os.mkdir(os.path.join('lib', 'modules'))
        os.chdir('lib/modules')
        shutil.copytree(os.path.join('/lib/modules/', self.kernel_ver),
                        self.kernel_ver, symlinks=True)
        os.chdir(self.curdir)

    def _copy_wlan_modules(self):
        path = ('lib/modules/' + self.kernel_ver +
                '/kernel/drivers/net/wireless/intel/iwlwifi')
        os.chdir(self.dirname)
        os.makedirs(os.path.join(path, 'dvm'))
        os.makedirs(os.path.join(path, 'mvm'), exist_ok=True)
        shutil.copy2(os.path.join('/', path, 'dvm', 'iwldvm.ko'),
                     os.path.join(path, 'dvm'))
        shutil.copy2(os.path.join('/', path, 'mvm', 'iwlmvm.ko'),
                     os.path.join(path, 'mvm'))
        shutil.copy2(os.path.join('/', path, 'iwlwifi.ko'), path)
        os.chdir(self.curdir)

    def _populate_busybox(self):
        os.chdir(os.path.join(self.dirname, 'bin'))
        output = subprocess.check_output(['busybox', '--list']).decode('utf-8')
        for command in output.split('\n'):
            if not command or command == 'busybox':
                continue
            os.symlink('busybox', command)

    def _copy_key(self, suffix=''):
        key_path = self.key_path
        if not os.path.exists(key_path):
            key_path = os.path.join(self.key_path + suffix)

        if not os.path.exists(key_path):
            self._cleanup()
            sys.stderr.write(f'Cannot find key(s) file for {self._drive}.\n')
            sys.exit(2)

        key_path = os.path.abspath(key_path)
        os.chdir(self.dirname)
        shutil.copy2(key_path, 'keys')
        os.chdir(self.curdir)

    def _generate_init(self):
        os.chdir(self.dirname)
        with open('init', 'w') as fobj:
            fobj.write(SHEBANG_ASH)
            fobj.write(f"UUID='{self.uuid}'\n")
            if self.key:
                fobj.write(f"KEY='/keys/{self.key}'\n")
            fobj.write(INIT)
            fobj.write(INIT_CMD)
            if self.disk_label:
                fobj.write(INIT_LABELED % {'label': self.disk_label})
            if self.sdcard:
                fobj.write(INIT_SD)
            fobj.write(INIT_OPEN)
            if self.disk_label or self.sdcard:
                fobj.write(DECRYPT_KEYDEV)
            if self.yk:
                fobj.write(DECRYPT_YUBICP % {'disk': self._drive})
            fobj.write(DECRYPT_PASSWORD)
            fobj.write(SWROOT)

        os.chmod('init', 0b111101101)
        os.chdir(self.curdir)

    def _mkcpio_arch(self):
        _fd, self.cpio_arch = tempfile.mkstemp(suffix='.cpio')
        os.close(_fd)
        _fd, scriptname = tempfile.mkstemp(suffix='.sh')
        os.close(_fd)
        os.chdir(self.dirname)
        with open(scriptname, 'w') as fobj:
            fobj.write(SHEBANG)
            fobj.write(MKCPIO % {'arch': self.cpio_arch})
        os.chmod(scriptname, 0b111101101)
        subprocess.call([scriptname])
        os.chdir(self.curdir)
        os.unlink(scriptname)

        os.chmod(self.cpio_arch, 0b110100100)

        if self.install:
            self._make_boot_links()
        else:
            shutil.move(self.cpio_arch, 'initramfs.cpio')

    def _make_boot_links(self):
        os.chdir('/boot')
        current = None
        old = None

        if os.path.exists('initramfs') and os.path.islink('initramfs'):
            current = os.readlink('initramfs')
            os.unlink('initramfs')

        if not current:
            shutil.move(self.cpio_arch, 'initramfs-' + self.kernel_ver)
            os.symlink('initramfs-' + self.kernel_ver, 'initramfs')
            return

        if os.path.exists('initramfs.old') and os.path.islink('initramfs.old'):
            old = os.readlink('initramfs.old')
            os.unlink('initramfs.old')
            os.unlink(old)

        shutil.move(current, current + '.old')
        os.symlink(current + '.old', 'initramfs.old')

        shutil.move(self.cpio_arch, 'initramfs-' + self.kernel_ver)
        os.symlink('initramfs-' + self.kernel_ver, 'initramfs')
        os.chdir(self.curdir)

    def _cleanup(self):
        shutil.rmtree(self.dirname)

    def build(self):
        self._make_dirs()
        self._copy_deps()
        self._copy_modules()
        # self._copy_wlan_modules()
        self._populate_busybox()
        if not self.no_key:
            self._copy_key()
        if self.yk:
            self._copy_key('.yk')
        self._generate_init()
        self._mkcpio_arch()
        self._cleanup()


def _disks_msg(msg=None):
    if not msg:
        sys.stdout.write('You need to create %s toml file with the '
                         'contents:\n\n'
                         '[name]\n'
                         'uuid = "disk-uuid"\n'
                         'key = "key-filename"\n'
                         '...\n' % CONF_PATH)
    else:
        sys.stdout.write(msg + '\n')


def _load_disks():
    try:
        with open(CONF_PATH, 'rb') as fobj:
            return tomllib.load(fobj)
    except IOError:
        _disks_msg()
        sys.exit(1)


def main():
    disks = _load_disks()
    if not disks:
        _disks_msg()
        sys.exit(3)

    parser = argparse.ArgumentParser(description="Generate initramfs. It "
                                     "contain only necesairy things to unlock "
                                     "encrypted partition (selectable by it's "
                                     "name) and boot from such partition.")

    parser.add_argument('-i', '--install', action='store_true',
                        help='Install initramfs in /boot. Link initramfs will '
                        'be created there and previous version will be '
                        'renamed with ".old" extension. Without this option, '
                        'initramfs will be generated in current directory.')
    parser.add_argument('-m', '--copy-modules', action='store_true',
                        help='Copy kernel modules into initramfs image.')
    parser.add_argument('-n', '--no-key', action='store_true',
                        help='Do not copy key file to the initramfs - '
                        'assuming SD card/usb stick is the only way to open '
                        'encrypted root.')
    parser.add_argument('-k', '--key-path', help='path to the location where '
                        'keys are stored')
    parser.add_argument('-d', '--disk-label', help='Provide disk label '
                        'to be read decription key from.')
    parser.add_argument('-s', '--sdcard', help='Use built in sdcard reader to '
                        'read from (hopefully) inserted card')
    parser.add_argument('-l', '--lvm', action='store_true',
                        help='Enable LVM in init.')
    parser.add_argument('-y', '--yubikey', action='store_true',
                        help='Enable Yubikey challenge-response in init.')
    parser.add_argument('drive', choices=disks.keys(), help='Drive name')

    args = parser.parse_args()
    if args.drive not in disks:
        _disks_msg(f'Drive {args.drive} not found in configuration')
        sys.exit(4)
    conf = Config(args.__dict__, disks)
    init = Initramfs(conf)
    init.build()


if __name__ == "__main__":
    main()
