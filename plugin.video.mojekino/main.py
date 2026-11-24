# -*- coding: utf-8 -*-
import sys, os, re, json, time, hashlib, uuid, socket, unicodedata
import urllib.parse, urllib.request, urllib.error
import xml.etree.ElementTree as ET
import difflib
import xbmc, xbmcaddon, xbmcgui, xbmcplugin, xbmcvfs

_addon = xbmcaddon.Addon()
_data_dir = xbmcvfs.translatePath('special://profile/addon_data/' + _addon.getAddonInfo('id'))
_creds_file = os.path.join(_data_dir, 'creds.json')
_conf_file = os.path.join(_data_dir, 'conf.json')
_cache_file = os.path.join(_data_dir, 'cache.json')
_lib_file = os.path.join(_data_dir, 'library.json')
_prog_file = os.path.join(_data_dir, 'progress.json')
_icon = os.path.join(_addon.getAddonInfo('path'), 'icon3.png')

TMDB_KEY = '44c9c4403a36b8eef3186db47ed91557'
TMDB = 'https://api.themoviedb.org/3'
IMG = 'https://image.tmdb.org/t/p/w342'
BASE = 'https://webshare.cz'
API = BASE + '/api/'
REALM = ':Webshare:'
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36')
HEADERS = {'User-Agent': UA, 'Referer': BASE + '/',
           'Content-Type': 'application/x-www-form-urlencoded',
           'Accept-Encoding': 'identity'}
MIN_INTERVAL = 1.2
BACKOFF = (2, 5, 12)
CACHE_TTL = 6 * 3600
ITOA64 = "./0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
GENRES = [(28, 'Akcia'), (12, 'Dobrodružné'), (16, 'Animované'), (35, 'Komédia'),
          (80, 'Krimi'), (18, 'Dráma'), (27, 'Horor'), (10749, 'Romantické'),
          (878, 'Sci-fi'), (53, 'Thriller'), (99, 'Dokument'), (14, 'Fantasy')]
QORDER = {'2160': 3, '1080': 2, '720': 1, '480': 0}

KRA_BASE = 'https://stream-cinema.online/kodi'
KRA_API = 'https://api.kra.sk'


def _to64(v, n):
    out = []
    while n > 0:
        n -= 1
        out.append(ITOA64[v & 0x3f])
        v >>= 6
    return ''.join(out)


def md5crypt(pw, salt):
    if isinstance(pw, str):
        pw = pw.encode()
    if isinstance(salt, str):
        salt = salt.encode()
    salt = salt.split(b'$', 1)[0][:8]
    ctx = bytearray(pw + b'$1$' + salt)
    final = hashlib.md5(pw + salt + pw).digest()
    for pl in range(len(pw), 0, -16):
        ctx.extend(final[:min(pl, 16)])
    i = len(pw)
    while i:
        ctx.append(0 if i & 1 else pw[0])
        i >>= 1
    final = hashlib.md5(ctx).digest()
    for i in range(1000):
        c = bytearray()
        c.extend(pw if (i & 1) else final[:16])
        if i % 3:
            c.extend(salt)
        if i % 7:
            c.extend(pw)
        c.extend(final[:16] if (i & 1) else pw)
        final = hashlib.md5(c).digest()
    return ('$1$' + salt.decode() + '$' +
            _to64((final[0] << 16) | (final[6] << 8) | final[12], 4) +
            _to64((final[1] << 16) | (final[7] << 8) | final[13], 4) +
            _to64((final[2] << 16) | (final[8] << 8) | final[14], 4) +
            _to64((final[3] << 16) | (final[9] << 8) | final[15], 4) +
            _to64((final[4] << 16) | (final[10] << 8) | final[5], 4) +
            _to64(final[11], 2))


def jload(p):
    try:
        with open(p) as f:
            return json.load(f)
    except Exception:
        return {}


def jsave(p, d):
    xbmcvfs.mkdirs(_data_dir)
    with open(p, 'w') as f:
        json.dump(d, f)


def conf():
    c = jload(_conf_file)
    c.setdefault('tmdb', TMDB_KEY)
    c.setdefault('pref', 'any')
    c.setdefault('minq', '1080')
    c.setdefault('maxsrc', 12)
    c.setdefault('kra_user', '')
    c.setdefault('kra_pass', '')
    return c


def creds():
    c = jload(_creds_file)
    if c.get('u') and c.get('p'):
        return c
    d = xbmcgui.Dialog()
    u = d.input('Webshare e-mail')
    if not u:
        return {}
    p = d.input('Webshare heslo', option=xbmcgui.ALPHANUM_HIDE_INPUT)
    if not p:
        return {}
    jsave(_creds_file, {'u': u, 'p': p})
    return {'u': u, 'p': p}


def cache_get(key):
    e = jload(_cache_file).get(key)
    if e and time.time() - e['ts'] < CACHE_TTL:
        return e['items']
    return None


def cache_put(key, items):
    d = jload(_cache_file)
    d[key] = {'ts': time.time(), 'items': items}
    if len(d) > 400:
        for k in sorted(d, key=lambda k: d[k]['ts'])[:120]:
            d.pop(k, None)
    jsave(_cache_file, d)


def lib_key(kind, t, y):
    return '%s|%s|%s' % (kind, norm(t), y)


def lib_all():
    return jload(_lib_file)


def lib_toggle(entry):
    lib = lib_all()
    k = lib_key(entry['kind'], entry['t'], entry.get('y', ''))
    if k in lib:
        lib.pop(k)
        jsave(_lib_file, lib)
        return False
    entry['added'] = time.time()
    entry['seen'] = 0
    lib[k] = entry
    jsave(_lib_file, lib)
    return True


def prog_set(show_id, show, season, ep):
    p = jload(_prog_file)
    p[show_id] = {'t': show, 'id': show_id, 's': season, 'e': ep, 'ts': time.time()}
    jsave(_prog_file, p)


class WsError(Exception):
    def __init__(self, code, msg=''):
        self.code = str(code)
        super().__init__(code + ': ' + msg)


class Ws:
    def __init__(self, user, pw):
        self.user, self.pw = user, pw
        self.tok = ''
        self._last = 0.0

    def _throttle(self):
        w = MIN_INTERVAL - (time.time() - self._last)
        if w > 0:
            time.sleep(w)
        self._last = time.time()

    def _call(self, fnct, data, retries=3):
        for a in range(retries):
            self._throttle()
            try:
                body = urllib.parse.urlencode(data).encode()
                req = urllib.request.Request(API + fnct + '/', data=body,
                                             headers=HEADERS, method='POST')
                with urllib.request.urlopen(req, timeout=20) as r:
                    root = ET.fromstring(r.read().decode('utf-8', 'replace'))
            except (urllib.error.URLError, socket.timeout, ET.ParseError) as e:
                if a == retries - 1:
                    raise WsError('network', str(e))
                time.sleep(BACKOFF[min(a, len(BACKOFF) - 1)])
                continue
            if root.findtext('status') == 'OK':
                return root
            err = root.findtext('error') or 'unknown'
            if err == 'Too many requests' and a < retries - 1:
                time.sleep(BACKOFF[min(a, len(BACKOFF) - 1)])
                continue
            raise WsError(err)
        raise WsError('network')

    def login(self):
        if self.tok:
            return self.tok
        salt = self._call('salt', {'username_or_email': self.user}).findtext('salt')
        enc = hashlib.sha1(md5crypt(self.pw, salt).encode()).hexdigest()
        digest = hashlib.md5((self.user + REALM + enc).encode()).hexdigest()
        tok = self._call('login', {'username_or_email': self.user, 'password': enc,
                                   'digest': digest, 'keep_logged_in': 1}).findtext('token')
        if not tok:
            raise WsError('login', 'bez tokenu')
        self.tok = tok
        return tok

    def search(self, what, limit=40):
        ck = 's:' + what
        hit = cache_get(ck)
        if hit is not None:
            return hit
        self.login()
        root = self._call('search', {'what': what, 'category': 'video',
                                     'sort': 'relevance', 'limit': limit,
                                     'offset': 0, 'wst': self.tok,
                                     'maybe_removed': 'true'})
        items = [{'ident': f.findtext('ident'), 'name': f.findtext('name'),
                  'size': int(f.findtext('size') or 0),
                  'protected': f.findtext('password') == '1'} for f in root.iter('file')]
        cache_put(ck, items)
        return items

    def play_url(self, ident, password=None):
        self.login()
        if self._call('file_protected', {'ident': ident, 'wst': self.tok}).findtext('protected') == '1' and not password:
            raise WsError('protected', 'heslo')
        data = {'ident': ident, 'wst': self.tok, 'download_type': 'video_stream',
                'device_uuid': _addon.getSetting('duuid')}
        if password:
            salt = self._call('file_password_salt', {'ident': ident, 'wst': self.tok}).findtext('salt')
            data['password'] = (hashlib.sha1(md5crypt(password, salt).encode()).hexdigest()
                                if salt else password)
        link = self._call('file_link', data).findtext('link')
        if not link:
            raise WsError('link', 'prazdny')
        return link + '|' + urllib.parse.urlencode({'Cookie': 'wst=' + self.tok,
                                                    'User-Agent': UA})


# ---------------- kra.sk ----------------

class KraError(Exception):
    pass


def kra_uid():
    c = conf()
    if not c.get('kra_uuid'):
        c['kra_uuid'] = uuid.uuid4().hex
        jsave(_conf_file, c)
    return c['kra_uuid']


def kra_http(url, payload=None, extra=None):
    h = {'User-Agent': UA, 'Accept': 'application/json',
         'Content-Type': 'application/json'}
    if extra:
        h.update(extra)
    body = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=body, headers=h,
                                 method='POST' if body else 'GET')
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return json.loads(r.read().decode('utf-8', 'replace'))
    except urllib.error.HTTPError as e:
        if e.code in (502, 503, 504):
            raise KraError('kra.sk práve neodpovedá (%d), skús neskôr' % e.code)
        raise KraError('HTTP %d' % e.code)
    except (urllib.error.URLError, socket.timeout) as e:
        raise KraError('sieť: %s' % str(e)[:80])


def kra_login():
    c = conf()
    u, p = c.get('kra_user'), c.get('kra_pass')
    if not u or not p:
        raise KraError('Chýba prihlásenie na kra.sk (Nastavenia)')
    r = kra_http(KRA_API + '/api/user/login',
                 {'data': {'username': u, 'password': p}})
    sid = r.get('session_id')
    if not sid:
        raise KraError('prihlásenie na kra.sk zlyhalo')
    uid = kra_uid()
    r2 = kra_http(KRA_BASE + '/auth/token?krt=' + urllib.parse.quote(sid) +
                  '&ver=2.0&uid=' + uid + '&lang=sk', extra={'X-Uuid': uid})
    tok = r2.get('token')
    if not tok:
        raise KraError('token nebol vydaný (možno čaká na schválenie)')
    c = conf()
    c['kra_sid'] = sid
    c['kra_token'] = tok
    c['kra_ts'] = time.time()
    jsave(_conf_file, c)
    return tok


def kra_auth(force=False):
    c = conf()
    if not force and c.get('kra_token'):
        return c['kra_token']
    return kra_login()


def kra_get(path, params=None):
    last = 'chyba'
    for attempt in (0, 1):
        tok = kra_auth(force=attempt == 1)
        prm = {'ver': '2.0', 'uid': kra_uid(), 'lang': 'sk'}
        if params:
            prm.update(params)
        url = KRA_BASE + path + '?' + urllib.parse.urlencode(prm)
        try:
            d = kra_http(url, extra={'X-Uuid': kra_uid(), 'X-AUTH-TOKEN': tok})
        except KraError as e:
            last = str(e)
            if 'HTTP 40' in last and attempt == 0:
                continue
            raise
        if 'error' in d:
            last = str(d.get('error'))
            if attempt == 0:
                continue
            raise KraError(last)
        sysd = d.get('system') or {}
        if 'msgERROR' in sysd:
            m = sysd['msgERROR']
            if isinstance(m, dict):
                i18 = m.get('i18n') or {}
                m = i18.get('sk') or i18.get('cs') or 'chyba'
            raise KraError(str(m))
        return d
    raise KraError(last)


def kra_link(ident):
    c = conf()
    sid = c.get('kra_sid')
    if not sid:
        kra_login()
        sid = conf().get('kra_sid')
    try:
        ui = kra_http(KRA_API + '/api/user/info', {'session_id': sid})
        dl = (ui.get('data') or {}).get('days_left')
        if dl is not None and int(dl) <= 0:
            raise KraError('kra.sk: predplatné skončilo')
    except KraError:
        raise
    except Exception:
        pass
    r = kra_http(KRA_API + '/api/file/download',
                 {'data': {'ident': ident}, 'session_id': sid})
    link = (r.get('data') or {}).get('link')
    if not link:
        raise KraError('odkaz sa nepodarilo získať')
    return link


def kra_render(d, cur):
    if cur == '/':
        add('Hľadať na kra.sk', 'krasearch', folder=True, art={'icon': _icon})
    strms = d.get('strms')
    if strms:
        for s in sorted(strms, key=lambda x: -QORDER.get(str(x.get('quality') or ''), 0)):
            label = '%s · %s · %s' % (s.get('quality') or '?',
                                      s.get('lang') or '', s.get('size') or '')
            add(label.strip(' ·'), 'krastream',
                {'ident': s.get('ident') or '', 'subs': s.get('subs') or ''},
                folder=False, play=True,
                info={'plot': (s.get('title') or '')[:200]})
        return
    for it in d.get('menu') or []:
        t = it.get('type') or ''
        url = it.get('url') or ''
        title = it.get('title') or ''
        if not url or not title or t in ('action', 'cmd'):
            continue
        if t == 'next':
            title = 'Ďalšia strana'
        info = it.get('info') or {}
        add(title, 'krabrowse', {'p': url}, folder=True,
            art=it.get('art') or {'icon': _icon},
            info={'plot': (info.get('plot') or '')[:300]})


def tmdb(path, **params):
    params['api_key'] = conf()['tmdb']
    params.setdefault('language', 'cs-CZ')
    url = TMDB + path + '?' + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode('utf-8', 'replace'))


def norm(s):
    s = unicodedata.normalize('NFKD', s or '').encode('ascii', 'ignore').decode()
    return re.sub(r'[^a-z0-9 ]+', ' ', s.lower())


def quality(name):
    n = name.lower()
    for k in ('2160', '1080', '720', '480'):
        if k in n:
            return k
    return '480'


def match_score(name, title, year):
    n = norm(name)
    m = re.search(r's(\d{2})e(\d{2})', norm(title))
    if m:
        s, e = m.group(1), m.group(2)
        if not ('s%se%s' % (s, e) in n or '%dx%s' % (int(s), e) in n
                or '%dx%d' % (int(s), int(e)) in n):
            return 0
        base = re.sub(r's\d{2}e\d{2}', ' ', norm(title))
    else:
        base = norm(title)
    toks = [t for t in base.split() if len(t) > 1]
    if not toks:
        return 0
    hit = sum(1 for t in toks if t in n)
    need = len(toks) if len(toks) <= 2 else -(-len(toks) * 7 // 10)
    if hit < need:
        return 0
    s = hit * 20
    years = re.findall(r'\b(19\d{2}|20\d{2})\b', n)
    if year:
        if year in years:
            s += 40
        elif years:
            if abs(int(years[0]) - int(year)) > 1:
                return 0
            s += 10
        else:
            s -= 10
    s += QORDER.get(quality(name), 0) * 12
    pref = conf().get('pref', 'any')
    if pref == 'cz':
        s += 30 if ('dabing' in n or re.search(r'\bcz\b', n)) else -20
    elif pref == 'sk':
        s += 30 if ('dabing' in n or re.search(r'\bsk\b', n)) else -20
    return s


def collect_sources(w, titles, year):
    titles = [t for t in titles if t]
    queries = []
    for t in titles:
        for suf in ('', 'cz dabing', 'dabing', 'titulky', 'cz', '4k', '2160p'):
            qq = ('%s %s %s' % (t, year, suf)).strip()
            if qq not in queries:
                queries.append(qq)
    queries = queries[:8]
    found = {}
    for query in queries:
        for f in w.search(query):
            found[f['ident']] = f
    scored = []
    for f in found.values():
        sc = max(match_score(f['name'], t, year) for t in titles)
        if sc > 0 and f['size'] > 30 * 1024 * 1024:
            scored.append((sc, f))
    scored.sort(key=lambda x: -x[0])
    minq = conf().get('minq', '1080')
    top = [x for x in scored if QORDER.get(quality(x[1]['name']), 0) >= QORDER.get(minq, 2)]
    return (top or scored)[:int(conf().get('maxsrc', 12))]


def fmt_size(b):
    for u in ('B', 'KB', 'MB', 'GB', 'TB'):
        if b < 1024:
            return '%d %s' % (b, u)
        b //= 1024
    return '%d PB' % b


def add(label, act, params=None, folder=True, art=None, info=None, play=False, ctx=None):
    qs = '?action=' + act
    if params:
        qs += '&' + urllib.parse.urlencode(params)
    li = xbmcgui.ListItem(label=label, offscreen=True)
    if art:
        li.setArt(art)
    if info:
        li.setInfo('video', info)
    if play:
        li.setProperty('IsPlayable', 'true')
    if ctx:
        li.addContextMenuItems(ctx)
    xbmcplugin.addDirectoryItem(H, _url + qs, li, folder)


def err(e):
    xbmcgui.Dialog().ok('Chyba', str(e)[:150])


H = int(sys.argv[1])
_url = sys.argv[0]
q = dict(urllib.parse.parse_qsl(sys.argv[2][1:]))
action = q.get('action')
page = int(q.get('page', 1))


def media_list(items, kind):
    for it in items:
        y = (it.get('release_date') or it.get('first_air_date') or '')[:4]
        title = it.get('title') or it.get('name')
        art = {'poster': IMG + it['poster_path']} if it.get('poster_path') else {'icon': _icon}
        prm = {'t': title, 'y': y, 'kind': kind}
        if it.get('original_title') or it.get('original_name'):
            prm['ot'] = it.get('original_title') or it.get('original_name')
        in_lib = lib_key(kind, title, y) in lib_all()
        ctx = [(('Odobrať z knižnice' if in_lib else 'Do knižnice'),
                'RunPlugin(%s?action=libtoggle&%s)' % (_url, urllib.parse.urlencode(prm)))]
        add('%s (%s)' % (title, y or '?'), 'sources', prm, folder=True, art=art,
            info={'plot': it.get('overview', '')[:400],
                  'year': int(y) if y.isdigit() else 0}, ctx=ctx)


try:
    if action == 'movies':
        add('Populárne', 'list', {'p': 'movie/popular', 'kind': 'movie'})
        add('Trending', 'list', {'p': 'trending/movie/week', 'kind': 'movie'})
        add('Žánre', 'genres')
        add('Hľadať film', 'search', {'kind': 'movie'})
        xbmcplugin.endOfDirectory(H)

    elif action == 'tv':
        add('Populárne', 'list', {'p': 'tv/popular', 'kind': 'tv'})
        add('Hľadať seriál', 'search', {'kind': 'tv'})
        xbmcplugin.endOfDirectory(H)

    elif action == 'genres':
        for gid, gname in GENRES:
            add(gname, 'genre', {'id': gid})
        xbmcplugin.endOfDirectory(H)

    elif action == 'genre':
        data = tmdb('/discover/movie', with_genres=q['id'], sort_by='popularity.desc', page=page)
        media_list(data.get('results', []), 'movie')
        if page < data.get('total_pages', 1) and page < 20:
            add('Ďalšia strana', 'genre', {'id': q['id'], 'page': page + 1})
        xbmcplugin.endOfDirectory(H)

    elif action == 'list':
        data = tmdb('/' + q['p'], page=page)
        media_list(data.get('results', []), q.get('kind', 'movie'))
        if page < data.get('total_pages', 1) and page < 20:
            add('Ďalšia strana', 'list', {'p': q['p'], 'kind': q.get('kind'), 'page': page + 1})
        xbmcplugin.endOfDirectory(H)

    elif action == 'search':
        what = xbmcgui.Dialog().input('Hľadať v TMDB')
        if what:
            kind = q.get('kind', 'movie')
            media_list(tmdb('/search/' + kind, query=what).get('results', []), kind)
        xbmcplugin.endOfDirectory(H)

    elif action == 'krabrowse':
        cur = q.get('p') or '/'
        kra_render(kra_get(cur), cur)
        xbmcplugin.setContent(H, 'videos')
        xbmcplugin.endOfDirectory(H)

    elif action == 'krasearch':
        what = xbmcgui.Dialog().input('Hľadať na kra.sk')
        if what:
            kra_render(kra_get('/Search/search-movies', {'search': what}), '/')
        xbmcplugin.setContent(H, 'videos')
        xbmcplugin.endOfDirectory(H)

    elif action == 'krastream':
        ident = q.get('ident')
        if not ident:
            xbmcplugin.setResolvedUrl(H, False, xbmcgui.ListItem())
            sys.exit()
        li = xbmcgui.ListItem(path=kra_link(ident))
        subs = q.get('subs') or ''
        if '/file/' in subs:
            try:
                li.setSubtitles([kra_link(subs.split('/file/')[-1].strip('/'))])
            except Exception:
                pass
        xbmcplugin.setResolvedUrl(H, True, li)

    elif action == 'libtoggle':
        e = {'kind': q.get('kind', 'movie'), 't': q['t'], 'y': q.get('y', ''),
             'id': q.get('id', ''), 'poster': q.get('poster', '')}
        added = lib_toggle(e)
        xbmcgui.Dialog().notification('MojeKino',
                                      'Pridané do knižnice' if added else 'Odobrané')

    elif action == 'seen':
        lib = lib_all()
        k = lib_key(q['kind'], q['t'], q.get('y', ''))
        if k in lib:
            lib[k]['seen'] = 0 if lib[k].get('seen') else 1
            jsave(_lib_file, lib)
        xbmc.executebuiltin('Container.Refresh')

    elif action == 'library':
        lib = lib_all()
        if not lib:
            add('— knižnica je prázdna —', 'library')
        for k, e in sorted(lib.items(), key=lambda x: -x[1].get('added', 0)):
            prm = {'t': e['t'], 'y': e.get('y', ''), 'kind': e['kind'], 'id': e.get('id', '')}
            art = {'poster': e['poster']} if e.get('poster') else {'icon': _icon}
            ctx = [(('Označiť ako nevidené' if e.get('seen') else 'Označiť ako videné'),
                    'RunPlugin(%s?action=seen&%s)' % (_url, urllib.parse.urlencode(prm))),
                   ('Odobrať z knižnice',
                    'RunPlugin(%s?action=libtoggle&%s)' % (_url, urllib.parse.urlencode(prm)))]
            add(e['t'], 'sources', prm, folder=True, art=art,
                info={'playcount': e.get('seen', 0),
                      'year': int(e['y']) if e.get('y', '').isdigit() else 0}, ctx=ctx)
        xbmcplugin.endOfDirectory(H)

    elif action == 'continue':
        p = jload(_prog_file)
        if not p:
            add('— zatiaľ nič —', 'continue')
        for sid, e in sorted(p.items(), key=lambda x: -x[1].get('ts', 0)):
            add('%s — S%02dE%02d' % (e['t'], e['s'], e['e']), 'episodes',
                {'id': sid, 't': e['t']})
        xbmcplugin.endOfDirectory(H)

    elif action == 'wssearch':
        what = xbmcgui.Dialog().input('Hľadať na Webshare', defaultt=q.get('q', ''))
        c = creds()
        if what and c:
            res = Ws(c['u'], c['p']).search(what)
            for f in sorted(res, key=lambda f: -QORDER.get(quality(f['name']), 0)):
                add(f['name'], 'play', {'ident': f['ident']}, folder=False, play=True,
                    info={'plot': fmt_size(f['size'])})
        xbmcplugin.endOfDirectory(H)

    elif action == 'sources':
        c = creds()
        if not c:
            xbmcplugin.endOfDirectory(H, succeeded=False)
            sys.exit()
        title, year = q['t'], q.get('y') or ''
        kind = q.get('kind', 'movie')
        if kind == 'tv' and q.get('id'):
            add('Epizódy', 'episodes', {'id': q['id'], 't': title}, folder=True)
        base = {'t': title, 'y': year, 'kind': kind, 'id': q.get('id', '')}
        res = collect_sources(Ws(c['u'], c['p']), [title, q.get('ot', '')], year)
        if not res:
            add('— nič sa nenašlo, skús iný názov —', 'sources', {'t': title})
            add('Hľadať ručne na Webshare', 'wssearch', {'q': title})
        else:
            add('▶ Prehrať najlepší zdroj', 'play',
                dict(base, ident=res[0][1]['ident']), folder=False, play=True)
            for sc, f in res:
                add(f['name'], 'play', dict(base, ident=f['ident']), folder=False,
                    play=True, info={'plot': fmt_size(f['size'])})
        xbmcplugin.endOfDirectory(H)

    elif action == 'episodes':
        data = tmdb('/tv/' + q['id'])
        for s in data.get('seasons', []):
            if s.get('season_number', 0) == 0:
                continue
            add('Séria %d (%d ep.)' % (s['season_number'], s.get('episode_count', 0)),
                'season', {'id': q['id'], 't': q['t'], 's': s['season_number']})
        xbmcplugin.endOfDirectory(H)

    elif action == 'season':
        data = tmdb('/tv/%s/season/%s' % (q['id'], q['s']))
        for e in data.get('episodes', []):
            add('%02d. %s' % (e['episode_number'], e.get('name') or ''),
                'sources', {'t': '%s S%02dE%02d' % (q['t'], int(q['s']), e['episode_number']),
                            'y': (e.get('air_date') or '')[:4], 'kind': 'tv', 'id': q['id']},
                folder=True, info={'plot': (e.get('overview') or '')[:300]})
        xbmcplugin.endOfDirectory(H)

    elif action == 'play':
        c = creds()
        if not c:
            xbmcplugin.setResolvedUrl(H, False, xbmcgui.ListItem())
            sys.exit()
        if q.get('kind') == 'tv' and q.get('id'):
            m = re.search(r's(\d{2})e(\d{2})', norm(q.get('t', '')))
            if m:
                prog_set(q['id'], re.sub(r'\s*S\d{2}E\d{2}', '', q['t']),
                         int(m.group(1)), int(m.group(2)))
        w = Ws(c['u'], c['p'])
        ident = q.get('ident')
        pw = None
        try:
            w.play_url(ident)
        except WsError as e:
            if e.code == 'protected':
                pw = xbmcgui.Dialog().input('Heslo k súboru', option=xbmcgui.ALPHANUM_HIDE_INPUT)
        try:
            xbmcplugin.setResolvedUrl(H, True, xbmcgui.ListItem(path=w.play_url(ident, pw)))
        except WsError as e:
            err(e)
            xbmcplugin.setResolvedUrl(H, False, xbmcgui.ListItem())

    elif action == 'settings':
        c = conf()
        d = xbmcgui.Dialog()
        pref = d.input('Dabing: any / cz / sk', defaultt=c['pref'])
        if pref in ('any', 'cz', 'sk'):
            c['pref'] = pref
        mq = d.input('Kvalita: 2160 / 1080 / 720 / 480', defaultt=c['minq'])
        if mq in ('2160', '1080', '720', '480'):
            c['minq'] = mq
        ku = d.input('kra.sk užívateľ (prázdne = nezmeniť)', defaultt=c.get('kra_user', ''))
        if ku != c.get('kra_user', ''):
            c['kra_user'] = ku
            c['kra_token'] = ''
            c['kra_sid'] = ''
        kp = d.input('kra.sk heslo (prázdne = nezmeniť)',
                     defaultt=c.get('kra_pass', ''), option=xbmcgui.ALPHANUM_HIDE_INPUT)
        if kp != c.get('kra_pass', ''):
            c['kra_pass'] = kp
            c['kra_token'] = ''
            c['kra_sid'] = ''
        jsave(_conf_file, c)
        d.ok('Nastavenia', 'Uložené: %s / %s' % (c['pref'], c['minq']))

    else:
        add('Filmy', 'movies')
        add('Seriály', 'tv')
        if conf().get('kra_user'):
            add('kra.sk', 'krabrowse', {'p': '/'}, art={'icon': _icon})
        add('Knižnica', 'library')
        add('Pokračovať v sledovaní', 'continue')
        add('Hľadať na Webshare', 'wssearch')
        add('Nastavenia', 'settings')
        xbmcplugin.endOfDirectory(H)

except (WsError, KraError) as e:
    err(e)
    xbmcplugin.endOfDirectory(H, succeeded=False)
except Exception as e:
    err(e)
    xbmcplugin.endOfDirectory(H, succeeded=False)
