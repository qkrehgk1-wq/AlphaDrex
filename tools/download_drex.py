"""Download pinned upstream artifacts; model stays outside source distributions."""
import hashlib
import json
import pathlib
import urllib.request
from concurrent.futures import ThreadPoolExecutor

root = pathlib.Path(__file__).resolve().parents[1]
repo = 'nace-ai/drex-v1.5-Q8_0'
revision = '37c2b59a5b8ecb95b19e6bfae2d4041e0de19808'
dest = root / 'models' / 'drex-v1.5-Q8_0'
dest.mkdir(parents=True, exist_ok=True)
metadata = json.load(urllib.request.urlopen(f'https://huggingface.co/api/models/{repo}/revision/{revision}?blobs=true'))
for item in metadata['siblings']:
    name = item['rfilename']
    if name not in ['LICENSE', 'README.md', 'drex-v1.5-Q8_0.gguf']:
        continue
    target = dest / name
    if not target.exists():
        print('Downloading', name, flush=True)
        url = f'https://huggingface.co/{repo}/resolve/{revision}/{name}'
        partial = target.with_suffix(target.suffix+'.part')
        if name.endswith('.gguf'):
            size = item['lfs']['size']
            chunk_size = 8*1024*1024
            with partial.open('wb') as out:
                out.truncate(size)
            def fetch(start):
                end = min(start+chunk_size,size)-1
                for attempt in range(4):
                    try:
                        req = urllib.request.Request(url+f'?chunk={start}',headers={'Range':f'bytes={start}-{end}'})
                        with urllib.request.urlopen(req,timeout=45) as response:
                            content = response.read(end-start+2)
                            if len(content)!=end-start+1 or response.status!=206:
                                raise RuntimeError('Invalid range response')
                        with partial.open('r+b') as out:
                            out.seek(start);out.write(content)
                        return
                    except Exception:
                        if attempt==3: raise
            with ThreadPoolExecutor(max_workers=4) as executor:
                for i,_ in enumerate(executor.map(fetch,range(0,size,chunk_size))):
                    if i%64==0: print('Downloaded MiB',round(min((i+1)*chunk_size,size)/1048576),flush=True)
        else:
            with urllib.request.urlopen(url, timeout=60) as response, partial.open('wb') as out:
                while chunk := response.read(1024*1024):
                    out.write(chunk)
        target.with_suffix(target.suffix+'.part').replace(target)
    checksum = hashlib.file_digest(target.open('rb'), 'sha256').hexdigest()
    expected = item.get('lfs', {}).get('sha256')
    if expected and expected != checksum:
        raise RuntimeError('SHA256 mismatch')
    print(name, target.stat().st_size, checksum, flush=True)
    if name.endswith('.gguf'):
        (dest/'provenance.json').write_text(json.dumps({'repo':repo, 'revision':revision, 'sha256':checksum,'expected_sha256':expected},indent=2))
