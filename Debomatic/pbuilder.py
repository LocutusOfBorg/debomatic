# Deb-o-Matic
#
# Copyright (C) 2007-2009 Luca Falavigna
#
# Author: Luca Falavigna <dktrkranz@ubuntu.com>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; version 3 of the License.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA 02110-1301, USA.

import os
import sys
from hashlib import sha256
from time import strftime
from urllib import urlopen
from Debomatic import locks

def setup_pbuilder(directory, configdir, distopts):
    if not os.path.exists(os.path.join(directory)):
        os.mkdir(os.path.join(directory))
    if not locks.pbuilderlock_acquire(distopts['distribution']):
        return False
    result = needs_update(directory, distopts['mirror'], distopts['distribution'])
    if result:
        if prepare_pbuilder(result, directory, configdir, distopts) == False:
            return False
        if not os.path.exists(os.path.join(directory, 'gpg')):
            os.mkdir(os.path.join(directory, 'gpg'))
        gpgfile = os.path.join(directory, 'gpg', distopts['distribution'])
        fd = os.open(gpgfile, os.O_WRONLY | os.O_CREAT, 0664)
        try:
            remote = urlopen('%s/dists/%s/Release.gpg' % (distopts['mirror'], distopts['distribution'])).read()
        except:
            print 'Unable to fetch %s/dists/%s/Release.gpg' % (distopts['mirror'], distopts['distribution'])
            locks.pbuilderlock_release(distopts['distribution'])
            return False
        os.write(fd, remote)
        os.close(fd)
    locks.pbuilderlock_release(distopts['distribution'])

def needs_update(directory, mirror, distribution):
    if not os.path.exists(os.path.join(directory, 'gpg')):
        os.mkdir(os.path.join(directory, 'gpg'))
    gpgfile = os.path.join(directory, 'gpg', distribution)
    if not os.path.exists(gpgfile):
        return 'create'
    try:		
        fd = os.open(gpgfile, os.O_RDONLY)
    except:
        return 'create'
    try:
        remote = urlopen('%s/dists/%s/Release.gpg' % (mirror, distribution)).read()
    except:
        print 'Unable to fetch %s/dists/%s/Release.gpg' % (mirror, distribution)
        return 'update'
    remote_sha = sha256()
    gpgfile_sha = sha256()
    remote_sha.update(remote)
    gpgfile_sha.update(os.read(fd, os.fstat(fd).st_size))
    os.close(fd)
    if remote_sha.digest() != gpgfile_sha.digest():
        return 'update'

def prepare_pbuilder(cmd, directory, configdir, distopts):
    if not os.path.exists(os.path.join(directory, 'build')):
        os.mkdir(os.path.join(directory, 'build'))
    if not os.path.exists(os.path.join(directory, 'aptcache')):
        os.mkdir(os.path.join(directory, 'aptcache'))
    if not os.path.exists(os.path.join(directory, 'logs')):
        os.mkdir(os.path.join(directory, 'logs'))
    if (os.system('pbuilder %(cmd)s --basetgz %(directory)s/%(distribution)s \
                  --override-config --configfile %(cfg)s --buildplace %(directory)s/build \
                  --aptcache "%(directory)s/aptcache" --logfile %(directory)s/logs/%(cmd)s.%(now)s >/dev/null 2>&1' \
                  % {'cmd': cmd, 'directory': directory, 'distribution': distopts['distribution'], \
                  'cfg': os.path.join(configdir, distopts['distribution']), 'now': strftime('%Y%m%d_%H%M')})):
        print 'pbuilder (%s) failed' % cmd
        locks.pbuilderlock_release(distopts['distribution'])
        return False

