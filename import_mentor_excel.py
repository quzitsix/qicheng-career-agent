"""Import the sixth-cohort mentor Excel with a strict public-field policy."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.mentor_excel import load_mentor_workbook, load_or_create_secret, write_private_json


def main() -> None:
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description='导师Excel脱敏导入；默认仅预检，--write写入网站目录。')
    parser.add_argument('file', type=Path)
    parser.add_argument('--write', action='store_true')
    parser.add_argument('--output', type=Path, default=root / 'data' / 'imported_mentors.json')
    parser.add_argument('--runtime-dir', type=Path, default=root / 'runtime')
    args = parser.parse_args()
    secret_path = args.runtime_dir / 'mentor_identity_secret'
    if args.write or secret_path.exists():
        secret = load_or_create_secret(secret_path)
    else:
        # Preview produces counts only, not persistent identities or source details.
        secret = b'dry-run-only-no-persistent-identity'
    try:
        catalog, mapping = load_mentor_workbook(args.file, secret)
        if args.write:
            write_private_json(args.output, catalog)
            write_private_json(args.runtime_dir / 'mentor_source_map.json', mapping)
        print(json.dumps({'mode': 'written' if args.write else 'dry-run', **catalog['import_report']}, ensure_ascii=False, indent=2))
    except (OSError, ValueError) as error:
        parser.exit(1, '导入失败：' + str(error) + '\n')


if __name__ == '__main__':
    main()
