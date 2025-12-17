mkinitramfs
===========

Simple script for generating initramfs for the encrypted root disks.

Usage
-----

- Create encrypted disk or partition using `cryptsetup`_
- Create ``~/.config/mkinitramfs.toml`` file with similar content to:

  .. code:: toml

     [name]
     uuid = "disk-uuid"
     key = "key-filename"

     ...

  where every entry have disk name (**name** in this case), which have at least
  two attributes - disk/partition UUID and key filename.
- Provide a key file for the disk/partition. Assumption is, that it is an
  encrypted file using `ccrypt`_ instead of plain file or password protected
  luks. Keys will be looked using provided path, i.e.

  .. code:: toml

     [laptop]
     uuid = "88b99002-028f-4744-94e7-45e4580e2ddd"
     key = "/full/path/to/the/laptop.key"

     [desktop]
     uuid = "23e31327-1411-491c-ab00-c36f74c441f1"
     key = "desktop.key"

     [pendrive]
     uuid = "1453a45e-ca3f-4d39-8fd7-a6a96873c25c"
     key = "../pendrive.key"

  so yes - it is possible to use key file in absolute or relative paths. If no
  key will be found, it's been looking for in path specified by
  ``--key-path | -k`` parameter, which by default is in
  ``$XDG_DATA_HOME/keys`` (usually it will be ``~/.local/share/keys``).
- Move ``mkinitramfs.py`` script to some location in your ``$PATH`` (like
  ``~/bin``)
- Invoke ``mkinitramfs.py`` script:

  .. code:: shell-session

     # mkinitramfs.py laptop

  that command will generate initramfs, copy key, and make appropriate change
  in ``init`` script and compress it with ``cpio``.

  Using ``--install | -i`` parameter, initramfs will be automatically installed
  on ``/boot`` with appropriate links. Note, that old images (they have
  ``.old`` suffix in the filename) will be removed in that case.

Configuration
-------------

Other than key path and device UUID, configuration can hold additional options
similar to those passed via commandline. Consider following example:

.. code:: toml

   [laptop]
   uuid = "88b99002-028f-4744-94e7-45e4580e2ddd"
   key_path = "/full/path/to/the/keys/dir"
   key = "laptop.key"
   yubikey = true
   dropbear = true
   ip = '192.168.0.1'
   gateway = '192.168.0.254'
   netmask = '24'
   user = 'gryf'
   authorized_keys = "/full/path/to/the/.ssh/authorized_keys"

This will inform mkinitramfs script, that dropbear and yubikey features are
enabled. Also for network related configuration, there are last three options.

The complete list of supported options is listed below:

- ``copy_modules``
- ``no_key``
- ``key_path``
- ``key``
- ``disk_label``
- ``sdcard``
- ``yubikey``
- ``dropbear``
- ``user``

Using key devices
-----------------

It is possible to use an SD card (if computer does have reader built-in) or old
plain USB pendrive. Currently support for the keys is limited to 4096 bytes,
and assumption that key is unencrypted - it helps with booting system
non-interactively.

Yubikey
-------

There is possibility for using key which is encrypted using response from
challenge response using `ykchalresp`_ command. The challenge here could be
any string, so the name of the device from config is used.

Dropbear
--------

To unlock LUKS root filesystem remotely `dropbear`_ is used. There are expected
configuration options in ``mkinitramfs.toml`` file:

- ``dropbear`` - true or false, false by default
- ``iface`` interface name - ``eth0`` by default
- ``ip`` - static IP address
- ``netmask`` - netmask for the network
- ``gateway`` - gateway for the network
- ``user`` - username used for logging in, ``root`` by default. Note, whatever
  username will be placed here, it will be ``root`` effectively anyway
- ``authorized_keys`` - path to ssh ``authorized_keys`` file. If there is no
  user set - which mens root username is used, by default it will look for the
  ``/root/.ssh/authorized_keys``

You'll need to put at least ``ip``, ``netmask``, ``gateway`` to make this work
with defaults, with assumption that interface is ``eth0`` and ``root`` user
have needed ``authorized_keys`` file. There is also ``askpass.c`` which origins
from `better-initramfs`_ project, included in this repository just for
preservation purposes.

Then execute script with flag ``-b`` which include dropbear part.:

.. code:: shell-session

   # mkinitramfs.py -b laptop

.. _ccrypt: https://sourceforge.net/projects/ccrypt/
.. _cryptsetup: https://gitlab.com/cryptsetup/cryptsetup/blob/master/README.md
.. _ykchalresp: https://github.com/Yubico/yubikey-personalization
.. _dropbear: https://matt.ucc.asn.au/dropbear/dropbear.html
.. _better-initramfs: https://bitbucket.org/piotrkarbowski/better-initramfs
