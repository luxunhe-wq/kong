"""Update repository About and Topics with an explicitly supplied administrator token."""
import argparse
import getpass
import json
import os
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--token-file', type=Path, help='Read the token from a private file outside the repository')
    args = parser.parse_args()
    metadata = json.loads((Path(__file__).resolve().parents[1] / '.github/repository-metadata.json').read_text())
    if args.token_file:
        token = args.token_file.read_text().strip()
    else:
        token = os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN')
        if not token and sys.stdin.isatty():
            token = getpass.getpass('GitHub token (not displayed): ').strip()
    if not token:
        raise SystemExit('GitHub administrator token required. Use --token-file or enter it locally; do not paste it into chat.')
    base = 'https://api.github.com/repos/' + metadata['repository']

    def request(path='', method='GET', body=None):
        headers = {'Accept':'application/vnd.github+json', 'X-GitHub-Api-Version':'2022-11-28',
                   'User-Agent':'kong-repository-metadata', 'Authorization':'Bearer ' + token}
        if body is not None:
            headers['Content-Type'] = 'application/json'
        req = Request(base + path, method=method, headers=headers,
                      data=json.dumps(body).encode() if body is not None else None)
        try:
            with urlopen(req, timeout=20) as response:
                return json.load(response)
        except HTTPError as error:
            raise SystemExit(f'GitHub returned HTTP {error.code}; check that the token has Administration: Read and write for {metadata["repository"]}.') from None
        except URLError:
            raise SystemExit('Cannot connect to GitHub API. Retry when the connection is available.') from None

    repository = request()
    if not repository.get('permissions', {}).get('admin'):
        raise SystemExit('The authenticated account must administer this repository.')
    about = request(method='PATCH', body={key:metadata[key] for key in ('description', 'homepage')})
    topics = request('/topics', method='PUT', body={'names':metadata['topics']})
    if any(about.get(key) != metadata[key] for key in ('description','homepage')) or set(topics['names']) != set(metadata['topics']):
        raise SystemExit('GitHub metadata did not match the requested values; inspect the repository settings.')
    print('About, website and Topics updated: https://github.com/' + metadata['repository'])


if __name__ == '__main__':
    main()
