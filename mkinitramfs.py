#!/usr/bin/env python
"""
Python2/3 compatible initrd generatin script
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile


XDG_CONFIG_HOME = os.getenv('XDG_CONFIG_HOME', os.path.expanduser('~/.config'))
XDG_DATA_HOME = os.getenv('XDG_DATA_HOME',
                          os.path.expanduser('~/.local/share'))
CONF_PATH = os.path.join(XDG_CONFIG_HOME, 'mkinitramfs.json')
KEYS_PATH = os.path.join(XDG_DATA_HOME, 'keys')
SHEBANG = "#!/bin/bash\n"
SHEBANG_ASH = "#!/bin/sh\n"
DEPS = """
DEPS=(
/bin/busybox
/usr/bin/ccrypt
/sbin/cryptsetup
%(lvm)s
)
"""
COPY_DEPS = """
for bin in ${DEPS[*]}; do
    cp $bin ./bin/
    ldd $bin >/dev/null || continue
    for lib in $(ldd $bin | sed -nre 's,.* (/.*lib.*/.*.so.*) .*,\\1,p' \\
        -e 's,.*(/lib.*/ld.*.so.*) .*,\\1,p')
    do
        mkdir -p .${lib%/*} && cp {,.}$lib
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

clear
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

# open shell
CMD=`cat /proc/cmdline`

for param in $CMD; do
    if [ "${param}" == "rescue" ]; then
        exec /bin/sh
    fi
done

# open encrypted root
for counter in $(seq 3); do
    sleep 1
    clear
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
    poweroff
    exit
fi

for i in 0 1 2 ; do
    ccrypt -c $KEY | cryptsetup open --allow-discards $DEVICE root
    ret=$?
    [ ${ret} -eq 0 ] && break
done

# get the tty back
rm /dev/tty
mv /dev/tty_bak /dev/tty

mount /dev/mapper/root /new-root

# restore hotplug events
echo > /proc/sys/kernel/hotplug

umount -l /proc /sys /dev

exec switch_root /new-root /sbin/init
"""


class Initramfs(object):
    def __init__(self, args, disks):
        self.lvm = args.lvm
        self.install = args.install
        self.disk_name = args.disk
        self.dirname = None
        self.copymodules = args.copy_modules
        self.key_path = args.key_path
        self.kernel_ver = os.readlink('/usr/src/linux').replace('linux-', '')
        self._make_tmp()
        self._disks = disks

    def _make_tmp(self):
        self.dirname = tempfile.mkdtemp(prefix='init_')
        self.curdir = os.path.abspath(os.curdir)

    def _make_dirs(self):
        os.chdir(self.dirname)
        for dir_ in ("bin", "dev", "etc", "keys", "lib64", "proc",
                     "run/cryptsetup", "sys", "tmp", "usr"):
            os.makedirs(os.path.join(self.dirname, dir_))

        for link, target in (('lib', 'lib64'), ('sbin', 'bin'),
                             ('linuxrc', 'bin/busybox')):
            os.symlink(target, link)
        os.chdir(self.curdir)

    def _copy_deps(self):
        os.chdir(self.dirname)
        fd, fname = tempfile.mkstemp(dir=self.dirname, suffix='.sh')
        os.close(fd)
        with open(fname, 'w') as fobj:
            lvm = '/sbin/lvscan\n/sbin/vgchange' if self.lvm else ''
            fobj.write(SHEBANG)
            fobj.write(DEPS % {'lvm': lvm})
            fobj.write(COPY_DEPS)

        os.chmod(fname, 0b111101101)
        subprocess.call([fname])
        os.unlink(fname)
        os.chdir(self.curdir)

    def _copy_modules(self):
        if not self.copymodules:
            return
        os.chdir(self.dirname)
        os.mkdir(os.path.join('lib', 'modules'))
        os.chdir('lib/modules')
        shutil.copytree(os.path.join('/lib/modules/', self.kernel_ver),
                        self.kernel_ver, symlinks=True)
        os.chdir(self.curdir)

    def _populate_busybox(self):
        os.chdir(os.path.join(self.dirname, 'bin'))
        output = subprocess.check_output(['busybox', '--list']).decode('utf-8')
        for command in output.split('\n'):
            if not command or command == 'busybox':
                continue
            os.symlink('busybox', command)

    def _copy_key(self):
        key_path = self._disks[self.disk_name]['key']
        if not os.path.exists(key_path):
            key_path = os.path.join(self.key_path,
                                    self._disks[self.disk_name]['key'])

        if not os.path.exists(key_path):
            self._cleanup()
            sys.stderr.write('Cannot find key file for %s.\n' % self.disk_name)
            sys.exit(2)

        key_path = os.path.abspath(key_path)
        os.chdir(self.dirname)
        shutil.copy2(key_path, 'keys')
        os.chdir(self.curdir)

    def _generate_init(self):
        os.chdir(self.dirname)
        with open('init', 'w') as fobj:
            fobj.write(SHEBANG_ASH)
            fobj.write("UUID='%s'\n" % self._disks[self.disk_name]['uuid'])
            fobj.write("KEY='/keys/%s'\n" % self._disks[self.disk_name]['key'])
            fobj.write(INIT)
        os.chmod('init', 0b111101101)
        os.chdir(self.curdir)

    def _mkcpio_arch(self):
        fd, self.cpio_arch = tempfile.mkstemp(suffix='.cpio')
        os.close(fd)
        fd, scriptname = tempfile.mkstemp(suffix='.sh')
        os.close(fd)
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
        self._populate_busybox()
        self._copy_key()
        self._generate_init()
        self._mkcpio_arch()
        self._cleanup()


def _disks_msg():
    sys.stdout.write('You need to create %s json file with the '
                     'contents:\n\n'
                     '{\n'
                     '   "name": {\n'
                     '       "uuid": "disk-uuid",\n'
                     '       "key": "key-filename"\n'
                     '   },\n'
                     '   ...\n'
                     '}\n' % CONF_PATH)


def _load_disks():
    try:
        with open(CONF_PATH) as fobj:
            return json.load(fobj)
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
    parser.add_argument('-k', '--key-path', help='path to the location where '
                        'keys are stored', default=KEYS_PATH)
    parser.add_argument('-l', '--lvm', action='store_true',
                        help='Enable LVM in init.')
    parser.add_argument('disk', choices=disks.keys(), help='Disk name')

    args = parser.parse_args()
    init = Initramfs(args, disks)
    init.build()


if __name__ == "__main__":
    main()
