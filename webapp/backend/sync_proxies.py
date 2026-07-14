from pathlib import Path
import json

try:
    from .db import init_db, import_proxies
except ImportError:  # pragma: no cover - allows script execution
    from db import init_db, import_proxies


def load_proxies_from_repo():
    project_root = Path(__file__).resolve().parents[2]
    # try JSON first
    json_path = project_root / 'proxies' / 'all' / 'data.json'
    txt_path = project_root / 'proxies' / 'all' / 'data.txt'
    proxies = []
    if json_path.exists():
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, list):
                    proxies = [str(x).strip() for x in data]
        except Exception:
            pass
    elif txt_path.exists():
        with open(txt_path, 'r', encoding='utf-8') as f:
            proxies = [l.strip() for l in f if l.strip()]
    return proxies


def main():
    init_db()
    proxies = load_proxies_from_repo()
    if not proxies:
        print('No proxies found in proxies/all/data.json or data.txt')
        return
    import_proxies(proxies)
    print(f'Imported {len(proxies)} proxies (duplicates ignored)')


if __name__ == '__main__':
    main()
