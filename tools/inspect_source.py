"""Read-only inspection of official SEACE public resources."""
import sys, urllib.request, urllib.parse
from pathlib import Path
url = sys.argv[1]
parsed = urllib.parse.urlparse(url)
if parsed.scheme not in ('http', 'https') or parsed.hostname not in ('prod4.seace.gob.pe', 'contratacionesabiertas.oece.gob.pe', 'prod2.seace.gob.pe'):
    raise ValueError('Official public source required')
req = urllib.request.Request(url, headers={'User-Agent':'Mozilla/5.0'})
with urllib.request.urlopen(req, timeout=15) as response:
    data = response.read(15000000)
Path('storage/source_inspection.txt').write_bytes(data)
print(data[:6000].decode('utf-8',errors='replace'))
