"""Import reviewed local CSV, JSON or Markdown into the demo catalog."""
import argparse
import json
from pathlib import Path
from app.catalog import Catalog

parser = argparse.ArgumentParser(description='导入已审核的导师/活动CSV、JSON或导师Markdown；导入后重启服务。')
parser.add_argument('files', nargs='+', type=Path)
args = parser.parse_args()
result = Catalog(Path(__file__).parent / 'data').import_files(args.files)
print(json.dumps(result, ensure_ascii=False, indent=2))
