import gzip, json, os, time
from collections import defaultdict

JOB_CODE = {
    'actor': 'a', 'actress': 'a',
    'director': 'd',
    'writer': 'w', 'screenwriter': 'w',
    'producer': 'p', 'film producer': 'p',
    'composer': 'c', 'film score composer': 'c',
    'cinematographer': 'ci',
    'editor': 'e', 'film editor': 'e',
    'creator': 'cr', 'showrunner': 'cr'
}

print("Loading title.basics to filter tvEpisode...")
episode_ids = set()
with gzip.open('title.basics.tsv.gz', 'rt', encoding='utf-8') as f:
    next(f)
    for line in f:
        tconst, title_type = line.split('\t')[:2]
        if title_type == 'tvEpisode':
            episode_ids.add(tconst)
print(f"Skip episodes: {len(episode_ids)}")

persons = defaultdict(list)
print("Loading principals...")
with gzip.open('title.principals.tsv.gz', 'rt', encoding='utf-8') as f:
    next(f)
    for line in f:
        try:
            tconst, _, nconst, category, _, _ = line.split('\t', 5)
        except: continue
        if tconst in episode_ids: continue
        code = JOB_CODE.get(category)
        if not code: continue
        persons[nconst].append([tconst, code])

print(f"Total persons with films: {len(persons)}")

shards = defaultdict(dict)
for nm, films in persons.items():
    numeric = nm[2:]
    shard = numeric[:5].ljust(5, '0')
    shards[shard][nm] = films

for shard, data in shards.items():
    prefix = shard[:2]
    out_dir = f'persons/{prefix}'
    os.makedirs(out_dir, exist_ok=True)
    with open(f'{out_dir}/{shard}.json', 'w', encoding='utf-8') as out:
        json.dump(data, out, separators=(',', ':'), ensure_ascii=False)

# version.json دقیقا مثل بقیه ریپوها
with open('version.json', 'w') as out:
    json.dump({
        "version": "v1",
        "updated": int(time.time()),
        "count": len(persons),
        "shards": len(shards)
    }, out)

print("Done - ready for gh-pages")
