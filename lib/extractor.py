import os
import py7zr
import zipfile
import subprocess
import gzip
import time
import shutil
import tarfile
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

class Extract:
    '''
        Extracting logs for a day in a respective service log
    '''

    def __init__(self, path, temp: bool = False):
        self.path = os.path.join(REPO_ROOT, path)
        self.log_path = os.path.join(self.path, 'logs')
        self.extract_path = os.path.join(self.path, 'extract')
        self.temp = temp
        self.error = 0
        self.outputpath = []
        self.outputpath.append(self.log_path)

    def __zip_file_list(self, path):
        dir_content = os.listdir(path)
        zip_list = []
        for content in dir_content:
            file_path = os.path.join(path, content)
            if not os.path.isfile(file_path):
                continue
            if py7zr.is_7zfile(file_path) or zipfile.is_zipfile(file_path):
                zip_list.append(file_path)
        zip_list.sort(reverse=False)
        return zip_list

    def __content_in_zip_file(self, zip_file):
        content = None
        if '.zip' in zip_file:
            try:
                content = zipfile.ZipFile(zip_file).namelist()
            except zipfile.BadZipFile:
                print("[-] Downloaded package has been corrupted Retriggering the Download")
                os.remove(zip_file)
                os.system('rm -rf ' + self.log_path + '/*')
                return None
        if '.7z' in zip_file:
            try:
                content = py7zr.SevenZipFile(zip_file).getnames()
            except py7zr.exceptions.Bad7zFile:
                print("[-] Downloaded package has been corrupted Retriggering the Download")
                os.remove(zip_file)
                os.system('rm -rf ' + self.log_path + '/*')
                return None
        if content is not None:
            content.sort(reverse=False)
        return content

    def extract(self, zip_file):
        '''
            Extract zip file into extract folder
        '''
        if '.zip' in zip_file:
            zipfile.ZipFile(zip_file).extractall(
                self.extract_path + '/' + zip_file.split('/')[-1].split('.')[0])
        if '.7z' in zip_file:
            py7zr.SevenZipFile(zip_file).extractall(
                self.extract_path + '/' + zip_file.split('/')[-1].split('.')[0])

    def mergelog(self):
        os.system('rm -rf ' + self.log_path + '/*')
        time.sleep(0.002)
        for zip_file in self.__zip_file_list(self.path):
            if ".tmp" in zip_file:
                print(f"{zip_file} may be corrupted..")
                continue
            contents = self.__content_in_zip_file(zip_file) # Content in a zip file
            if contents is None:
                print(f"{zip_file} may be corrupted..")
                continue
                # self.error = 1
                # break
            self.extract(zip_file)
            os.makedirs(self.log_path, exist_ok=True)

            for content in contents:
                partial_log_path = os.path.join(
                    self.extract_path + '/' + zip_file.split('/')[-1].split('.')[0], content)
                if 'trace_' in partial_log_path:
                    continue

                if 'syslog_monitor' in partial_log_path and partial_log_path.endswith('.log'):
                    syslog_monitor_dir = os.path.join(self.log_path, 'syslogs')
                    os.makedirs(syslog_monitor_dir, exist_ok=True)
                    dest_log_path = os.path.join(syslog_monitor_dir, os.path.basename(partial_log_path))
                    with open(partial_log_path, 'r', encoding='iso8859-15') as partial_log, open(dest_log_path, 'a', encoding='iso8859-15') as dest_log:
                        dest_log.write(partial_log.read())
                        dest_log.write('\n')
                    continue
                
                if 'syslog' in partial_log_path:
                    self.outputpath.append(partial_log_path)
                    os.makedirs(self.path + '/syslog', exist_ok=True)
                    tar_result = subprocess.run(
                        ['tar', 'xf', partial_log_path, '-C', self.path + '/syslog'],
                        capture_output=True,
                        text=True,
                    )
                    syslog_root = os.path.join(self.path, 'syslog', 'var', 'log')
                    if tar_result.returncode != 0:
                        print(f"[-] Skipping invalid syslog archive: {partial_log_path}")
                        if tar_result.stderr:
                            print(tar_result.stderr.strip())
                        continue
                    if not os.path.isdir(syslog_root):
                        print(f"[-] Syslog archive extracted without expected var/log path: {partial_log_path}")
                        continue
                    syslog_path = list(os.walk(syslog_root))
                    if not syslog_path:
                        print(f"[-] No syslog files found after extraction: {partial_log_path}")
                        continue
                    for syslog in syslog_path[0][2]:
                        sysloggz = os.path.join(syslog_path[0][0], syslog)
                        if '.gz' in sysloggz:
                            syslogtxt = os.path.join(syslog_path[0][0], '.'.join(syslog.split('.')[:-1]))
                            syslogtxt = syslogtxt.replace(':', '-')
                            with gzip.open(sysloggz, 'rb') as gzsys, open(syslogtxt, 'wb') as txtsys:
                                txtsys.write(gzsys.read())
                            os.remove(sysloggz)
                    continue


                dest_log_path = os.path.join(
                    self.log_path, content.split('/')[0] + '.log')
                if ".log.log" in dest_log_path:
                    dest_log_path = dest_log_path[:-4]
                
                with open(partial_log_path, 'r', encoding='iso8859-15') as partial_log, open(dest_log_path, 'a', encoding='iso8859-15') as dest_log:
                    dest_log.write(partial_log.read())
                    dest_log.write('\n')


            if self.temp == False:
                os.system('rm -rf ' + self.extract_path + '/' +
                          zip_file.split('/')[-1].split('.')[0])

        if self.error == 0:
            return 0
        else:
            return 1

    @staticmethod
    def extract_ota(ota_package_path, files, dest_dir):
        """
            Extract only `files` (matched by file name) from an OTA package
            (.tar.gz or .7z) into dest_dir. Packages nested under an ota_packages/ folder
            (e.g. D475 nd_app_setup.tar.gz) are searched too.
            Returns the list of extracted file paths.
        """
        if not os.path.exists(ota_package_path):
            raise FileNotFoundError(f'OTA package not exist in this location {ota_package_path}')

        os.makedirs(dest_dir, exist_ok=True)
        wanted = set(files)
        extracted = []
        if py7zr.is_7zfile(ota_package_path):
            Extract._extract_from_7z(ota_package_path, wanted, dest_dir, extracted)
        else:
            with tarfile.open(ota_package_path, 'r:gz') as tar:
                aliases = {}
                Extract._extract_from_tar(tar, wanted, dest_dir, extracted, aliases)
                wanted.update(aliases.values())  # links whose target was not found

        if wanted:
            print(f"[-] {', '.join(sorted(wanted))} not found in {ota_package_path}")
        return extracted

    @staticmethod
    def _extract_from_7z(path, wanted, dest_dir, extracted):
        with py7zr.SevenZipFile(path) as archive:
            # Shallowest match per file name, e.g. bagheera_config.ini at the root
            targets = {}
            for name in sorted(archive.getnames(), key=lambda n: n.count('/')):
                base = os.path.basename(name)
                if base in wanted and base not in targets:
                    targets[base] = name
            if not targets:
                return
            # A wanted file can be a symlink in the package (nd_config.ini -> nd_config_US.ini),
            # stored either as a link or as a small file holding only the target's name:
            # keep the file it points to, under the wanted name
            names = set(archive.getnames())
            with tempfile.TemporaryDirectory(dir=dest_dir) as tmp:
                archive.extract(path=tmp, targets=list(targets.values()))
                links = {}
                for base, name in targets.items():
                    path = os.path.join(tmp, name)
                    if os.path.islink(path):
                        link = os.readlink(path)
                    elif os.path.getsize(path) <= 256:
                        with open(path, encoding='latin-1') as f:
                            link = f.read().strip()
                    else:
                        continue
                    resolved = os.path.normpath(os.path.join(os.path.dirname(name), link))
                    if link and '\n' not in link and resolved != name and resolved in names:
                        links[base] = resolved
                if links:
                    archive.reset()
                    archive.extract(path=tmp, targets=list(set(links.values())))
                for base, name in targets.items():
                    dest_path = os.path.join(dest_dir, base)
                    src = os.path.join(tmp, links.get(base, name))
                    if os.path.lexists(dest_path):
                        os.remove(dest_path)
                    shutil.move(src, dest_path) if not os.path.islink(src) else shutil.copyfile(src, dest_path)
                    os.chmod(dest_path, 0o644)  # archive keeps it read-only
                    extracted.append(dest_path)
                    wanted.discard(base)

    @staticmethod
    def _extract_from_tar(tar, wanted, dest_dir, extracted, aliases=None):
        """aliases: {file a wanted symlink points to: the wanted name}, filled as links are met
        (nd_config.ini -> nd_config_US.ini keeps the target under the wanted name)."""
        aliases = {} if aliases is None else aliases
        for member in tar:
            if not wanted and not aliases:
                return
            name = os.path.basename(member.name)
            if member.issym() and name in wanted:
                aliases[os.path.basename(member.linkname)] = name
                wanted.discard(name)
                continue
            if not member.isfile():
                continue
            if name in wanted or name in aliases:
                for target in [n for n in (name if name in wanted else None, aliases.pop(name, None)) if n]:
                    dest_path = os.path.join(dest_dir, target)
                    with tar.extractfile(member) as src, open(dest_path, 'wb') as dst:
                        shutil.copyfileobj(src, dst)
                    extracted.append(dest_path)
                    wanted.discard(target)
            elif '/ota_packages/' in f'/{member.name}' and name.endswith('.tar.gz'):
                with tar.extractfile(member) as src, tarfile.open(fileobj=src, mode='r|gz') as inner:
                    Extract._extract_from_tar(inner, wanted, dest_dir, extracted, aliases)
