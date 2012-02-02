# Deb-o-Matic
#
# Copyright (C) 2007-2012 Luca Falavigna
# Copyright (C) 2010 Alessio Treglia
#
# Author: Luca Falavigna <dktrkranz@debian.org>
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
from hashlib import sha256
from lockfile import FileLock
from re import findall, split
from subprocess import call, PIPE
from time import strftime
from urllib2 import Request, urlopen, HTTPError, URLError

from gpg import GPG
from modules import Module


class Build:

    def __init__(self, opts, log, package=None, dsc=None,
                 debopts=None, distribution=None, origin=None):
        self.log = log
        self.e = self.log.t
        self.w = self.log.w
        (self.opts, self.rtopts, self.conffile) = opts
        self.package = package
        self.dscfile = dsc
        self.debopts = debopts
        self.distribution = distribution
        self.origin = origin
        self.cmd = None
        self.distopts = {}
        self.files = set()
        self.packagedir = self.opts.get('default', 'packagedir')
        self.full = False
        self.uploader = None

    def acquire_lock(self):
        self.lockfile = FileLock('/var/run/debomatic.%s' %
                                 (os.path.basename(self.configfile)))
        self.lockfile.acquire()

    def build(self):
        if self.dscfile:
            self.files.add(self.dscfile)
        self.parse_distribution_options()
        if self.distribution in self.runtime_option('distblacklist'):
            self.remove_files()
            self.e(_('Distribution %s is disabled' % self.distribution))
        self.fetch_missing_files()
        self.setup_pbuilder()
        self.build_package()

    def build_package(self):
        mod = Module((self.opts, self.rtopts, self.conffile))
        uploader_email = ''
        packageversion = os.path.splitext(os.path.basename(self.dscfile))[0]
        builddir = os.path.join(self.buildpath, 'pool', packageversion)
        self.w(_('Building %s') % os.path.basename(self.dscfile))
        if not os.path.exists(builddir):
            os.mkdir(builddir)
        if self.uploader:
            uploader_email = self.uploader[1]
        mod.execute_hook('pre_build', {'cfg': self.configfile,
                                       'directory': self.buildpath,
                                       'distribution': self.distribution,
                                       'dsc': self.dscfile,
                                       'opts': self.opts,
                                       'package': packageversion,
                                       'uploader': uploader_email})
        builder = self.opts.get('default', 'builder')
        if builder == 'cowbuilder':
            base = '--basepath'
        else:
            base = '--basetgz'
        debopts = ' '.join((self.get_build_options(),
                            self.get_compression(),
                            self.get_changelog_versions(),
                            self.get_orig_tarball()))
        with open(os.path.join(self.buildpath, 'pool', packageversion,
                  packageversion + '.buildlog'), 'w') as fd:
            call([builder, '--build', '--override-config',
                 base, '%s/%s' % (self.buildpath, self.distribution),
                 '--buildplace', '%s/build' % self.buildpath,
                 '--buildresult', '%s/pool/%s' %
                 (self.buildpath, packageversion),
                 '--aptcache', '%s/aptcache' % self.buildpath,
                 '--debbuildopts', '%s' % debopts,
                 '--hookdir', self.opts.get('default', 'pbuilderhooks'),
                 '--configfile', self.configfile, self.dscfile],
                 stdout=fd, stderr=fd)
        mod.execute_hook('post_build', {'cfg': self.configfile,
                                        'directory': self.buildpath,
                                        'distribution': self.distribution,
                                        'dsc': self.dscfile,
                                        'opts': self.opts,
                                        'package': packageversion,
                                        'uploader': uploader_email})
        self.remove_files()

    def fetch_missing_files(self):
        filename = self.dscfile if self.dscfile else self.package
        packagename = os.path.basename(filename).split('_')[0]
        for filename in self.files:
            if not self.dscfile:
                if filename.endswith('.dsc'):
                    self.dscfile = filename
                    break
        with open(self.dscfile, 'r') as fd:
            data = fd.read()
        for entry in findall('\s\w{32}\s\d+\s(\S+)', data):
            if not os.path.exists(os.path.join(self.packagedir, entry)):
                for component in self.distopts['ocomponents'].split():
                    request = Request('%s/pool/%s/%s/%s/%s' %
                                      (self.distopts['origin'], component,
                                       findall('^lib\S|^\S', packagename)[0],
                                       packagename, entry))
                    try:
                        data = urlopen(request).read()
                        break
                    except (HTTPError, URLError):
                        data = None
                if data:
                    with open(os.path.join(self.packagedir, entry), 'w') as e:
                        e.write(data)
            if not (os.path.join(self.packagedir, entry)) in self.files:
                self.files.add(os.path.join(self.packagedir, entry))

    def get_build_options(self):
        if self.debopts:
            return '-B -m"%s"' % self.debopts
        else:
            return ''

    def get_changelog_versions(self):
        version = ''
        if self.full:
            with open(os.path.join(self.packagedir, self.package), 'r') as fd:
                data = fd.read()
            try:
                version = '-v%s~' % findall(' \S+ \((\S+)\) \S+; ', data)[-1]
            except IndexError:
                pass
        return version

    def get_compression(self):
        compression = ''
        ext = {'.gz': 'gzip', '.bz2': 'bzip2', '.xz': 'xz'}
        for pkgfile in self.files:
            if os.path.exists(pkgfile):
                if findall('(.*\.debian\..*)', pkgfile):
                    try:
                        compression = '-Z%s' % \
                                       ext[os.path.splitext(pkgfile)[1]]
                    except IndexError:
                        pass
        return compression

    def get_orig_tarball(self):
        sa = ''
        if self.packagepath:
            with open(self.packagepath, 'r') as fd:
                data = fd.read()
            for file in findall('\s\w{32}\s\d+\s\S+\s\S+\s(.*)', data):
                if '.orig.' in file:
                    sa = '-sa'
                    break
        return sa

    def needs_update(self):
        if not os.path.exists(os.path.join(self.buildpath, 'gpg')):
            os.mkdir(os.path.join(self.buildpath, 'gpg'))
        gpgfile = os.path.join(self.buildpath, 'gpg', self.distribution)
        if not os.path.exists(gpgfile):
            self.cmd = 'create'
            return
        if self.distribution in self.runtime_option('alwaysupdate'):
            self.cmd = 'update'
            return
        uri = '%s/dists/%s/Release.gpg' % (self.distopts['mirror'],
                                           self.distribution)
        try:
            remote = urlopen(uri).read()
        except HTTPError:
            self.w(_('Unable to fetch %s') % uri)
            self.cmd = update
            return
        remote_sha = sha256()
        gpgfile_sha = sha256()
        remote_sha.update(remote)
        try:
            with open(gpgfile, 'r') as fd:
                gpgfile_sha.update(fd.read())
        except OSError:
            self.cmd = 'create'
            return
        if remote_sha.digest() != gpgfile_sha.digest():
            self.cmd = 'update'

    def parse_distribution_options(self):
        conf = {'components': ('[^#]?COMPONENTS="?(.*[^"])"?\n', 'COMPONENTS'),
                'debootstrap': ('[^#]?DEBOOTSTRAP="?(.*[^"])"?\n',
                                'DEBOOTSTRAP'),
                'distribution': ('[^#]?DISTRIBUTION="?(.*[^"])"?\n',
                                 'DISTRIBUTION'),
                'mirror': ('[^#]?MIRRORSITE="?(.*[^"])"?\n', 'MIRRORSITE'),
                'origin': ('[^#]?MIRRORSITE="?(.*[^"])"?\n', 'MIRRORSITE'),
                'ocomponents': ('[^#]?COMPONENTS="?(.*[^"])"?\n',
                                'COMPONENTS')}
        if self.full:
            try:
                with open(self.packagepath, 'r') as fd:
                    data = fd.read()
            except IOError:
                self.e(_('Unable to open %s') % self.packagepath)
            try:
                distro = findall('Distribution:\s+(\w+)', data)[0]
            except IndexError:
                self.e(_('Bad .changes file: %s') % self.packagepath)
            self.distopts['distribution'] = distro.lower()
            self.distribution = self.distopts['distribution']
        self.buildpath = os.path.join(self.packagedir, self.distribution)
        self.configfile = os.path.join(self.opts.get('default', 'configdir'),
                                       self.distribution)
        try:
            with open(self.configfile) as fd:
                data = fd.read()
        except IOError:
            self.log.e(_('Unable to open %s') % self.configfile)
        for elem in conf.keys():
            try:
                if not elem in self.distopts or not self.distopts[elem]:
                    self.distopts[elem] = findall(conf[elem][0], data)[0]
            except IndexError:
                self.e(_('Please set %(parm)s in %s(conf)s') %
                       {'parm': conf[elem][0], 'conf': conf})
        if self.origin:
            originconfig = os.path.join(self.opts.get('default', 'configdir'),
                                        self.origin)
            try:
                with open(originconfig) as fd:
                    data = fd.read()
            except IOError:
                self.log.e(_('Unable to open %s') % originconfig)
            for elem in ('origin', 'ocomponents'):
                try:
                    self.distopts[elem] = findall(conf[elem][0], data)[0]
                except IndexError:
                    self.e(_('Please set %(parm)s in %s(conf)s') %
                           {'parm': conf[elem][0], 'conf': conf})
        else:
            self.origin = self.distribution

    def prepare_pbuilder(self):
        for d in ('aptcache', 'build', 'logs', 'pool'):
            if not os.path.exists(os.path.join(self.buildpath, d)):
                os.mkdir(os.path.join(self.buildpath, d))
        for f in ('Packages', 'Release'):
            repo_file = os.path.join(self.buildpath, 'pool', f)
            if not os.path.exists(repo_file):
                with open(repo_file, 'w') as fd:
                    pass
        call(['gzip', '-9', '-f',
              os.path.join(self.buildpath, 'pool', 'Packages')])
        builder = self.opts.get('default', 'builder')
        if builder == 'cowbuilder':
            base = '--basepath'
        else:
            base = '--basetgz'
        with open(os.path.join(self.buildpath, 'logs',
                  '%s.%s' % (self.cmd, strftime('%Y%m%d_%H%M'))), 'w') as fd:
            if call([builder, '--%s' % self.cmd, '--override-config',
                    base, '%s/%s' % (self.buildpath, self.distribution),
                    '--buildplace', '%s/build' % self.buildpath,
                    '--aptcache', '%s/aptcache' % self.buildpath,
                    '--configfile', '%s' % self.configfile],
                    stdout=fd, stderr=fd):
                self.release_lock()
                self.e(_('%(builder)s %(cmd)s failed') %
                       {'builder': builder, 'cmd': self.cmd})

    def release_lock(self):
        self.lockfile.release()

    def remove_files(self):
        for pkgfile in self.files:
            if os.path.exists(pkgfile):
                os.remove(pkgfile)

    def runtime_option(self, option):
        self.rtopts.read(self.conffile)
        if self.rtopts.has_option('runtime', option):
            return self.rtopts.get('runtime', option).split()
        else:
            return ()

    def setup_pbuilder(self):
        if not os.path.exists(os.path.join(self.buildpath)):
            os.mkdir(os.path.join(self.buildpath))
        self.acquire_lock()
        self.needs_update()
        if self.cmd:
            self.prepare_pbuilder()
            gpgfile = os.path.join(self.buildpath, 'gpg',
                                   self.distopts['distribution'])
            uri = '%s/dists/%s/Release.gpg' % (self.distopts['mirror'],
                                               self.distopts['distribution'])
            try:
                remote = urlopen(uri).read()
            except HTTPError:
                self.release_lock()
                self.e(_('Unable to fetch %s') % uri)
            with open(gpgfile, 'w') as fd:
                fd.write(remote)
        self.release_lock()


class FullBuild(Build):

    def run(self):
        self.full = True
        self.packagepath = os.path.join(self.packagedir, self.package)
        self.files.add(self.packagepath)
        self.w(_('Processing %s') % self.package)
        try:
            with open(self.packagepath, 'r') as fd:
                data = fd.read()
        except IOError:
            self.e(_('Unable to open %s') % self.packagepath)
        try:
            for entry in findall('\s\w{32}\s\d+\s\S+\s\S+\s(.*)', data):
                self.files.add(os.path.join(self.packagedir, entry))
        except IndexError:
            self.e(_('Bad .changes file: %s') % self.packagepath)
        gpg = GPG(self.opts, self.packagepath)
        if gpg.gpg:
            if gpg.sig:
                self.uploader = gpg.sig
            else:
                self.remove_files()
                self.e(gpg.error)
        self.build()
