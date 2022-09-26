mkinitramfs
===========

Simple script for generating initramfs for the encrypted root disks.

Usage
-----

- Create encrypted disk or partition using `cryptsetup`_
- Create ``~/.config/mkinitramfs/disks.json`` file with similar content to:

  .. code:: json

     {
         "name": {
            "uuid": "disk-uuid",
            "key": "key-filename"
         },
         ...
     }

  where every entry have disk name (**name** in this case), which have two
  attributes - disk/partition UUID and key filename.
- Provide a key file for the disk/partition. Assumption is, that it is an
  encrypted file using `ccrypt`_ instead of plain file or password protected
  luks. Keys will be looked using provided path, i.e.

  .. code:: json

     {
         "laptop": {
            "uuid": "88b99002-028f-4744-94e7-45e4580e2ddd",
            "key": "/full/path/to/the/laptop.key"
         },
         "desktop": {
            "uuid": "23e31327-1411-491c-ab00-c36f74c441f1",
            "key": "desktop.key"
         },
         "pendrive": {
            "uuid": "1453a45e-ca3f-4d39-8fd7-a6a96873c25c",
            "key": "../pendrive.key"
         }
     }

  so yes - it is possible to use key file in absolute or relative paths. If no
  key will be found, it's been looking for in path specified by
  ``--key-path | -k`` parameter, which by default is in
  ``$XDG_CONFIG_HOME/mkinitramfs/keys`` (usually in
  ``~/.config/mkinitramfs/keys``.
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


.. _ccrypt: https://sourceforge.net/projects/ccrypt/
.. _cryptsetup: https://gitlab.com/cryptsetup/cryptsetup/blob/master/README.md
.. _ykchalresp: https://github.com/Yubico/yubikey-personalization
