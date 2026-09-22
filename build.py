import gzip, json, os
from collections import defaultdict

# مپ شغل به یک حرف برای سبک شدن - مثل t,n,w که تو awards کردی
JOB_CODE = {
    'actor': 'a', 'actress': 'a',
    'director': 'd',
    'writer': 'w', 'screenwriter': 'w',
    'producer': 'p', 'film producer': 'p',
    'composer': 'c',
    'cinematographer': 'ci',
    'editor': 'e',
    'creator': 'cr', 'showrunner': 'cr'
}

# مرحله 1: title.basics رو بخون تا بفهمیم کدوم tt اپیزوده
print("Loading title.basics...")
episode_ids = set()
with gzip.open('title.basics.tsv.gz', 'rt', encoding='utf-8') as f:
    next(f)
    for line in f:
        parts = line.split('\t')
        tconst = parts[0]
        title_type = parts[1] # movie, tvSeries, tvEpisode...
        if title_type == 'tvEpisode':
            episode_ids.add(tconst)

print(f"Episodes to skip: {len(episode_ids)}")

# مرحله 2: principals رو بخون و nm -> [[tt, jobCode]]
persons = defaultdict(list)
print("Loading title.principals...")
with gzip.open('title.principals.tsv.gz', 'rt', encoding='utf-8') as f:
    next(f)
    for line in f:
        tconst, _, nconst, category, _, _ = line.split('\t', 5)
        if tconst in episode_ids:
            continue # اپیزود سریال رو نباید ذخیره کنیم
        if category not in JOB_CODE:
            continue
        persons[nconst].append([tconst, JOB_CODE[category]])

print(f"Total persons: {len(persons)}")

# مرحله 3: شارد بندی و ذخیره
os.makedirs('persons', exist_ok=True)
shards = defaultdict(dict)

for nm, films in persons.items():
    numeric = nm[2:] # 0000151
    shard = numeric[:5].ljust(5, '0') # 00015
    shards[shard][nm] = films

for shard, data in shards.items():
    prefix = shard[:2]
    out_dir = f'persons/{prefix}'
    os.makedirs(out_dir, exist_ok=True)
    with open(f'{out_dir}/{shard}.json', 'w') as out:
        json.dump(data, out, separators=(',', ':'))

# version.json مثل بقیه ریپوهات
with open('version.json', 'w') as out:
    import time
    json.dump({"version": "v1", "updated": int(time.time())}, out)

print("Done!")
