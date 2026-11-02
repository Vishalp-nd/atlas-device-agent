"""
Fetch bagheera_config.ini for one or more OTA versions.

Downloads ota_packages/<product>/<ota_version>/package.tar.gz from s3
(idms-staging, falling back to idms-production; D475 9.x versions come from
s3://netradyne-sharing/Products/Qualcomm_D475/Unified_OS_OTA/<ota_version>/nd_app_setup.tar.gz),
saves bagheera_config.ini to INPUT/<ota_version>/bagheera_config.ini and
deletes the package.
The product folder is inferred from the version (e.g. 5.x -> bagheera3)
unless --product is given.

Usage:
  python lib/ota_config.py --ota 5.6.9.rc.4 [5.6.8.rc.5 ...] [--product bagheera3]
"""

import argparse

from downloader import OTADownloader


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--ota', nargs='+', required=True, help='One or more OTA versions')
    parser.add_argument('--product', help='s3 product folder, e.g. bagheera3 (default: inferred from version)')
    parser.add_argument('--files', nargs='+', default=['bagheera_config.ini'], help='Files to keep from the package')
    args = parser.parse_args()

    for ota_version in args.ota:
        try:
            paths = OTADownloader(ota_version, args.product).fetch_files(args.files)
        except (FileNotFoundError, ValueError) as e:
            print(f'[-] {ota_version}: {e}')
            continue
        for path in paths:
            print(f'[+] {ota_version}: saved {path}')


if __name__ == '__main__':
    main()
