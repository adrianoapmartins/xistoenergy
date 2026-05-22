"""
extend_analysis.py
==================
Estende a análise Abril-Novembro 2025 para incluir Dezembro 2025 – Março 2026.

O que faz:
  1. Lê os dois novos ficheiros CSV (Dez+Jan e Fev+Mar), somando todos os dispositivos
  2. Descarrega preços OMIE horários para Dez 2025 – Mar 2026
  3. Descarrega temperatura de Gamil, Barcelos (Open-Meteo ERA5) para esses meses
  4. Calcula custos: tarifário fixo, Ibelectra Família, Ibelectra Amigo (por hora/dia/mês)
  5. Actualiza energia_comparacao.html com todos os novos dados

Corre:
    cd ~/Downloads/Portugas
    python3 extend_analysis.py
"""

import re, json, time, sys, subprocess, urllib.request
from pathlib import Path
from datetime import date, timedelta
from collections import defaultdict

# ──────────────────────────────────────────────────────────────
# CONFIGURAÇÃO
# ──────────────────────────────────────────────────────────────
BASE = Path.home() / "Library/Mobile Documents/com~apple~CloudDocs" / "2025 Legrand Apr - Nov Energy Exports"
HTML_PATH = Path(__file__).parent / "energia_comparacao.html"
OUT_PATH  = Path(__file__).parent / "new_months_data.json"
CACHE_DIR = Path(__file__).parent / "_omie_cache"
CACHE_DIR.mkdir(exist_ok=True)

CSV_FILES = [
    BASE / "2025 Electricity Dezembro 546.14€ 2142kWh, Janeiro 558.70€ 2507kWh.csv",
    BASE / "2026 Electricity Fevereiro 325.64€ 1858 kWh, Março 220.99€ 1261kWh.csv",
]

# Facturas reais fornecidas pelo utilizador
ACTUAL = {
    '2025-12': {'kwh': 2142,  'paid': 546.14, 'name': 'Dezembro'},
    '2026-01': {'kwh': 2507,  'paid': 558.70, 'name': 'Janeiro'},
    '2026-02': {'kwh': 1858,  'paid': 325.64, 'name': 'Fevereiro'},
    '2026-03': {'kwh': 1261,  'paid': 220.99, 'name': 'Março'},
}

# Parâmetros Ibelectra calibrados em Abr-Nov 2025
# fam_€_per_kwh = (FIXED_c + OMIE_FACTOR × omie_€mwh) / 100
FIXED_c     = 10.17    # c€/kWh  (rede + impostos + VAT, componente fixa)
OMIE_FACTOR = 0.1304   # c€/kWh por €/MWh de OMIE
AMI_DELTA   = 0.002863 # €/kWh extra Amigo vs Família

# Open-Meteo: Gamil, Barcelos
LAT, LON = 41.533, -8.617

MONTH_NAMES = {
    '2025-12': 'Dezembro', '2026-01': 'Janeiro',
    '2026-02': 'Fevereiro', '2026-03': 'Março'
}

# ──────────────────────────────────────────────────────────────
# 1. LER CSVs
# ──────────────────────────────────────────────────────────────
def find_nlg_col(lines):
    """
    Detecta a coluna Wh do dispositivo NLG (Legrand Gateway = total da casa).

    A estrutura do CSV Legrand é simétrica: na linha de produtos, cada device
    ocupa [nome, tipo, vazio], e na linha de dados ocupa [bill, wh, vazio].
    O índice onde 'NLG' aparece na linha de produtos é exactamente o índice
    da coluna Wh correspondente na linha de dados.

    Devolve o índice da coluna Wh do NLG, ou None se não existir.
    """
    for line in lines:
        parts = line.split(',')
        for i, p in enumerate(parts):
            if p.strip() == 'NLG':
                return i  # mesmo índice na linha de dados
    return None

def parse_csv(path):
    """
    Lê ficheiro Legrand CSV (30-min, múltiplos dispositivos).
    Usa a coluna NLG (Legrand Gateway = total da casa) se disponível,
    caso contrário usa a 1ª coluna Wh (dispositivo principal).
    Devolve dict: {date_str → {hour → {'wh': float}}}
    """
    text = path.read_text(encoding='utf-8-sig')
    lines = text.splitlines()

    # Encontrar linha do cabeçalho real (com 'Timestamp')
    header_idx = next(i for i, l in enumerate(lines) if 'Timestamp' in l)

    # Detectar coluna NLG
    nlg_col = find_nlg_col(lines[:header_idx])
    if nlg_col is not None:
        print(f"    Usando coluna NLG (total casa) → índice {nlg_col}")
    else:
        nlg_col = 3  # fallback: 1ª coluna Wh (formato antigo Abr-Nov)
        print(f"    NLG não encontrado, usando col 3 (formato antigo)")

    result = defaultdict(lambda: defaultdict(lambda: {'wh': 0.0}))

    for line in lines[header_idx + 1:]:
        line = line.strip()
        if not line:
            continue
        parts = line.split(',')
        if len(parts) <= nlg_col:
            continue

        # datetime: col 1  →  "2025/12/01 00:15:00"
        dt_str = parts[1].strip('"')
        try:
            date_part, time_part = dt_str.split(' ')
            y, mo, d = date_part.split('/')
            hh = time_part.split(':')[0]
            date_key = f"{y}-{mo}-{d}"
            hour = int(hh)
        except:
            continue

        try:
            wh = float(parts[nlg_col]) if parts[nlg_col] else 0.0
        except:
            wh = 0.0

        result[date_key][hour]['wh'] += wh

    print(f"  ✓ {path.name[:40]}... → {sum(len(v) for v in result.values())} dias")
    return result

# ──────────────────────────────────────────────────────────────
# 2. DESCARREGAR OMIE
# ──────────────────────────────────────────────────────────────
def fetch_omie_day(ds):
    """Devolve lista de 24 preços (€/MWh, hora 0–23) ou None."""
    d = ds.replace('-', '')
    cache = CACHE_DIR / f"marginalpdbcpt_{d}.1"

    if cache.exists():
        raw = cache.read_text(encoding='latin-1')
    else:
        # OMIE: tentar vários formatos de URL (o formato mudou ao longo do tempo)
        URLS = [
            f"https://www.omie.es/pt/file-download?parents=marginalpdbcpt&filename=marginalpdbcpt_{d}.1",
            f"https://www.omie.es/en/file-download?parents=marginalpdbcpt&filename=marginalpdbcpt_{d}.1",
            f"https://www.omie.es/en/file-download?parents%5B0%5D=%2F&parents%5B1%5D=Mercado+Diario&parents%5B2%5D=1.+Precios&parents%5B3%5D=Precios+horarios+del+mercado+diario+en+Portugal&elem=marginalpdbcpt_{d}.1",
            f"https://www.omie.es/en/file-download?parents[]=omiedatosftp&parents[]=marginalpdbcpt&elem=marginalpdbcpt_{d}.1",
        ]
        raw = None
        for url in URLS:
            try:
                req = urllib.request.Request(url, headers={
                    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
                    'Accept': 'text/html,application/xhtml+xml,*/*',
                    'Accept-Language': 'pt-PT,pt;q=0.9,en;q=0.8',
                    'Referer': 'https://www.omie.es/',
                })
                with urllib.request.urlopen(req, timeout=20) as r:
                    content = r.read()
                    # Tentar decode em latin-1 e utf-8
                    for enc in ('latin-1', 'utf-8', 'cp1252'):
                        try:
                            raw = content.decode(enc)
                            break
                        except:
                            continue
                if raw and len(raw) > 200 and ';' in raw:
                    cache.write_text(raw, encoding='latin-1')
                    break
                raw = None
            except Exception:
                raw = None
                continue

        if raw is None:
            return None

    prices = {}
    for line in raw.splitlines():
        line = line.strip().rstrip(';')
        parts = [p.strip().replace(',', '.') for p in line.split(';')]
        try:
            if len(parts) == 6 and re.match(r'^\d{4}$', parts[0]):
                hour = int(parts[3])
                if 1 <= hour <= 24:
                    prices[hour] = float(parts[5])
        except:
            continue

    if len(prices) < 20:
        return None
    return [prices.get(h, None) for h in range(1, 25)]  # índice 0 = hora 1 = 00:00–01:00

def download_omie_range(start_ds, end_ds):
    """
    Devolve dict: {date_str → [24 prices]}.
    Usa cache local em _omie_cache/ — só descarrega dias em falta.
    """
    d = date.fromisoformat(start_ds)
    e = date.fromisoformat(end_ds)
    n = (e - d).days + 1

    # Verificar quantos já estão em cache
    cached = sum(1 for i in range(n)
                 if (CACHE_DIR / f"marginalpdbcpt_{(d + timedelta(days=i)).strftime('%Y%m%d')}.1").exists())
    to_fetch = n - cached

    if to_fetch == 0:
        print(f"  ✓ {n} dias OMIE em cache local — sem necessidade de download")
    else:
        print(f"  {cached} dias em cache, a descarregar {to_fetch} em falta...")

    out = {}
    cur = d
    i = 0
    while cur <= e:
        ds = str(cur)
        prices = fetch_omie_day(ds)
        if prices:
            out[ds] = prices
        i += 1
        if to_fetch > 0 and i % 10 == 0:
            print(f"    {i}/{n}...")
        time.sleep(0.05 if (CACHE_DIR / f"marginalpdbcpt_{cur.strftime('%Y%m%d')}.1").exists() else 0.3)
        cur += timedelta(days=1)

    print(f"  ✓ {len(out)}/{n} dias OMIE prontos")
    return out

# ──────────────────────────────────────────────────────────────
# 3. DESCARREGAR TEMPERATURA
# ──────────────────────────────────────────────────────────────
def fetch_temperature(start_ds, end_ds):
    """
    Descarrega temperatura horária ERA5 de Open-Meteo para Gamil, Barcelos.
    Devolve dict: {date_str → [24 temps °C]}
    """
    url = (
        f"https://archive-api.open-meteo.com/v1/archive"
        f"?latitude={LAT}&longitude={LON}"
        f"&start_date={start_ds}&end_date={end_ds}"
        f"&hourly=temperature_2m&timezone=Europe%2FLisbon"
    )
    print(f"  A descarregar temperatura {start_ds} → {end_ds}...")
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read())
        times = data['hourly']['time']
        temps = data['hourly']['temperature_2m']
        # agrupar por data
        by_date = defaultdict(list)
        for t, v in zip(times, temps):
            by_date[t[:10]].append(v)
        print(f"  ✓ Temperatura: {len(by_date)} dias")
        return dict(by_date)
    except Exception as e:
        print(f"  ⚠ Temperatura falhou: {e}")
        return {}

# ──────────────────────────────────────────────────────────────
# 4. CALCULAR DADOS
# ──────────────────────────────────────────────────────────────
def build_month_data(month_key, days_raw, omie_prices, temp_by_date):
    """
    Constrói todos os dados para um mês:
    - days_data: {date → [[wh, bill, cost_fam, price_mwh], ×24]}
    - monthly summary
    - heatmap (avg OMIE por hora)
    - cons_data (avg Wh por hora)
    - temp_monthly (avg temp por hora)
    - temp_daily (min/max/mean por dia)
    """
    actual = ACTUAL[month_key]
    actual_rate_c = actual['paid'] / actual['kwh'] * 100  # c€/kWh efectivo

    # filtrar apenas dias do mês correcto
    day_keys = sorted(k for k in days_raw if k[:7] == month_key)

    # Verificar se o total medido coincide com a factura real (sanity check)
    total_measured_wh = sum(
        days_raw[dk].get(h, {}).get('wh', 0.0)
        for dk in day_keys
        for h in range(24)
    )
    measured_kwh = total_measured_wh / 1000.0
    scale = 1.0  # sem escala — a coluna NLG já é o total real
    diff_pct = abs(measured_kwh - actual['kwh']) / actual['kwh'] * 100
    if diff_pct > 3:
        print(f"    ⚠ Discrepância: medido {measured_kwh:.0f} kWh vs factura {actual['kwh']} kWh ({diff_pct:.1f}%)")

    days_data = {}
    daily_kwh, daily_paid_l, daily_fam_l = [], [], []
    omie_by_hour = defaultdict(list)
    cons_by_hour = defaultdict(list)
    temp_hourly_month = {}
    temp_daily_month = {}

    for dk in day_keys:
        hours_raw = days_raw[dk]
        omie_day = omie_prices.get(dk, [None]*24)
        temp_day = temp_by_date.get(dk, [None]*24)

        # preencher gaps OMIE com média do dia
        valid = [p for p in omie_day if p is not None]
        omie_fill = sum(valid)/len(valid) if valid else 80.0
        omie_day_filled = [p if p is not None else omie_fill for p in omie_day]

        day_hours = []
        day_kwh = day_paid = day_fam = 0.0

        for h in range(24):
            wh = hours_raw.get(h, {}).get('wh', 0.0)
            kwh = wh / 1000.0
            omie_p = omie_day_filled[h]

            bill    = kwh * actual_rate_c / 100.0
            cost_fam = kwh * (FIXED_c + OMIE_FACTOR * omie_p) / 100.0

            day_hours.append([round(wh, 1), round(bill, 4), round(cost_fam, 4), round(omie_p, 2)])
            day_kwh  += kwh
            day_paid += bill
            day_fam  += cost_fam

            omie_by_hour[h].append(omie_p)
            cons_by_hour[h].append(wh)

        days_data[dk] = day_hours
        daily_kwh.append(round(day_kwh, 2))
        daily_paid_l.append(round(day_paid, 2))
        daily_fam_l.append(round(day_fam, 2))

        # temperatura diária
        valid_temps = [t for t in temp_day if t is not None]
        if valid_temps:
            temp_daily_month[dk] = {
                'min':  round(min(valid_temps), 1),
                'max':  round(max(valid_temps), 1),
                'mean': round(sum(valid_temps)/len(valid_temps), 1)
            }
        # temperatura horária
        temp_hourly_month[dk] = [round(t, 1) if t is not None else None for t in temp_day[:24]]

    # totais do mês
    total_kwh  = sum(daily_kwh)
    total_paid = sum(daily_paid_l)
    total_fam  = sum(daily_fam_l)
    total_ami  = total_fam + (total_kwh * AMI_DELTA)

    # médias horárias para heatmap e CONS_DATA
    avg_omie_by_hour = [round(sum(omie_by_hour[h])/len(omie_by_hour[h]), 1) if omie_by_hour[h] else 0.0 for h in range(24)]
    avg_cons_by_hour = [round(sum(cons_by_hour[h])/len(cons_by_hour[h]), 1) if cons_by_hour[h] else 0.0 for h in range(24)]
    avg_omie_month   = round(sum(avg_omie_by_hour)/24, 1)

    # temperatura média mensal por hora (para TEMP_MONTHLY)
    all_temps_by_hour = defaultdict(list)
    for dk in day_keys:
        for h, t in enumerate(temp_by_date.get(dk, [None]*24)):
            if t is not None:
                all_temps_by_hour[h].append(t)
    temp_monthly_avg_by_hour = [
        round(sum(all_temps_by_hour[h])/len(all_temps_by_hour[h]), 1) if all_temps_by_hour[h] else None
        for h in range(24)
    ]
    temp_monthly_avg = round(
        sum(v for v in temp_monthly_avg_by_hour if v is not None) /
        sum(1 for v in temp_monthly_avg_by_hour if v is not None), 1
    ) if any(v is not None for v in temp_monthly_avg_by_hour) else None

    monthly_entry = {
        'month': month_key,
        'name': ACTUAL[month_key]['name'],
        'kwh': round(total_kwh),
        'paid': round(total_paid, 2),
        'fam':  round(total_fam, 2),
        'ami':  round(total_ami, 2),
        'avg_omie': avg_omie_month,
        'actual_rate': round(actual_rate_c, 2),
        'fam_rate':    round(total_fam / total_kwh * 100, 2) if total_kwh > 0 else 0,
        'ami_rate':    round(total_ami / total_kwh * 100, 2) if total_kwh > 0 else 0,
        'daily_kwh':  daily_kwh,
        'daily_paid': daily_paid_l,
        'daily_fam':  daily_fam_l,
    }

    return {
        'days_data':     {month_key: days_data},
        'monthly':       monthly_entry,
        'heatmap':       {month_key: avg_omie_by_hour},
        'cons_data':     {month_key: avg_cons_by_hour},
        'temp_monthly':  {month_key: temp_monthly_avg_by_hour},
        'temp_hourly':   {month_key: temp_hourly_month},
        'temp_daily':    temp_daily_month,
        'temp_monthly_avg': temp_monthly_avg,
    }

# ──────────────────────────────────────────────────────────────
# 5. ACTUALIZAR HTML
# ──────────────────────────────────────────────────────────────
def update_html(new_data):
    html = HTML_PATH.read_text(encoding='utf-8')

    # ── DATA ──
    # Extrair e actualizar const DATA
    m = re.search(r'(const DATA = )(\{.*?\});', html, re.DOTALL)
    if m:
        data = json.loads(m.group(2))
        # Substituir (ou adicionar) os novos meses — garante que dados antigos/errados são removidos
        new_month_keys = {md['month'] for md in new_data['monthly_list']}
        data['monthly'] = [mo for mo in data['monthly'] if mo['month'] not in new_month_keys]
        data['monthly'].extend(new_data['monthly_list'])
        data['monthly'].sort(key=lambda mo: mo['month'])
        # Actualizar heatmap
        data['heatmap'].update(new_data['heatmap'])
        # Actualizar totais
        data['totals']['paid'] = round(sum(m['paid'] for m in data['monthly']), 2)
        data['totals']['fam']  = round(sum(m['fam']  for m in data['monthly']), 2)
        data['totals']['ami']  = round(sum(m['ami']  for m in data['monthly']), 2)
        data['totals']['kwh']  = round(sum(m['kwh']  for m in data['monthly']))
        new_json = json.dumps(data, ensure_ascii=False, separators=(',', ':'))
        html = html[:m.start()] + 'const DATA = ' + new_json + ';' + html[m.end():]
        print("  ✓ DATA actualizado")
    else:
        print("  ✗ Não encontrei const DATA")

    # ── CONS_DATA ──
    m = re.search(r'(const CONS_DATA = )(\{.*?\});', html, re.DOTALL)
    if m:
        cons = json.loads(m.group(2))
        cons.update(new_data['cons_data'])
        new_json = json.dumps(cons, ensure_ascii=False, separators=(',', ':'))
        html = html[:m.start()] + 'const CONS_DATA = ' + new_json + ';' + html[m.end():]
        print("  ✓ CONS_DATA actualizado")

    # ── DAYS_DATA ──
    m = re.search(r'(const DAYS_DATA = )(\{.*?\});', html, re.DOTALL)
    if m:
        dd = json.loads(m.group(2))
        dd.update(new_data['days_data'])
        new_json = json.dumps(dd, ensure_ascii=False, separators=(',', ':'))
        html = html[:m.start()] + 'const DAYS_DATA = ' + new_json + ';' + html[m.end():]
        print("  ✓ DAYS_DATA actualizado")

    # ── TEMP_MONTHLY ──
    m = re.search(r'(const TEMP_MONTHLY = )(\{.*?\});', html, re.DOTALL)
    if m:
        tm = json.loads(m.group(2))
        tm.update(new_data['temp_monthly'])
        new_json = json.dumps(tm, ensure_ascii=False, separators=(',', ':'))
        html = html[:m.start()] + 'const TEMP_MONTHLY = ' + new_json + ';' + html[m.end():]
        print("  ✓ TEMP_MONTHLY actualizado")

    # ── TEMP_HOURLY ──
    m = re.search(r'(const TEMP_HOURLY\s*=\s*)(\{.*?\});', html, re.DOTALL)
    if m:
        th = json.loads(m.group(2))
        th.update(new_data['temp_hourly'])
        new_json = json.dumps(th, ensure_ascii=False, separators=(',', ':'))
        html = html[:m.start()] + 'const TEMP_HOURLY = ' + new_json + ';' + html[m.end():]
        print("  ✓ TEMP_HOURLY actualizado")
    else:
        print("  ✗ TEMP_HOURLY não encontrado")

    # ── TEMP_DAILY ──
    m = re.search(r'(const TEMP_DAILY\s*=\s*)(\{.*?\});', html, re.DOTALL)
    if m:
        td = json.loads(m.group(2))
        td.update(new_data['temp_daily'])
        new_json = json.dumps(td, ensure_ascii=False, separators=(',', ':'))
        html = html[:m.start()] + 'const TEMP_DAILY = ' + new_json + ';' + html[m.end():]
        print("  ✓ TEMP_DAILY actualizado")
    else:
        print("  ✗ TEMP_DAILY não encontrado")

    # ── TEMP_MONTHLY_AVG ──
    m = re.search(r'(const TEMP_MONTHLY_AVG = )(\[.*?\]);', html, re.DOTALL)
    if m:
        tma = json.loads(m.group(2))
        tma.extend(new_data['temp_monthly_avg_list'])
        new_json = json.dumps(tma, ensure_ascii=False, separators=(',', ':'))
        html = html[:m.start()] + 'const TEMP_MONTHLY_AVG = ' + new_json + '; // aligned with DATA.monthly' + html[m.end():]
        print("  ✓ TEMP_MONTHLY_AVG actualizado")

    # ── shortNames / longNames ──
    short_add = ',"2025-12":"Dez","2026-01":"Jan","2026-02":"Fev","2026-03":"Mar"}'
    long_add  = ',"2025-12":"Dezembro","2026-01":"Janeiro","2026-02":"Fevereiro","2026-03":"Março"}'
    html = re.sub(
        r'(const shortNames\s*=\s*\{[^}]+)(\})',
        lambda m: m.group(0) if '"2025-12"' in m.group(0) else m.group(1) + short_add,
        html
    )
    html = re.sub(
        r'(const longNames\s*=\s*\{[^}]+)(\})',
        lambda m: m.group(0) if '"2025-12"' in m.group(0) else m.group(1) + long_add,
        html
    )
    print("  ✓ shortNames/longNames actualizados")

    # ── Hero: datas e totais ──
    totals = new_data['totals']
    total_save = totals['paid'] - totals['fam']
    save_pct   = total_save / totals['paid'] * 100

    html = html.replace(
        'Consumo doméstico em Gamil · Abril a Novembro 2025',
        'Consumo doméstico em Gamil · Abril 2025 a Março 2026'
    )
    # Hero cards – substituir valores (formato €X.XXX)
    def fmt_k(v):
        return f'€{v:,.0f}'.replace(',', '.')
    old_title = 'Comparação Tarifários Electricidade 2025'
    html = html.replace(old_title, 'Comparação Tarifários Electricidade 2025–2026')

    print("  ✓ Hero e título actualizados")

    HTML_PATH.write_text(html, encoding='utf-8')
    print(f"\n✅ HTML actualizado: {HTML_PATH}")
    print(f"   Total pago:      {fmt_k(totals['paid'])}")
    print(f"   Total Família:   {fmt_k(totals['fam'])}")
    print(f"   Total Amigo:     {fmt_k(totals['ami'])}")
    print(f"   Poupança Família:{fmt_k(total_save)} ({save_pct:.0f}%)")

# ──────────────────────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────────────────────
def main():
    print("=" * 60)
    print("Extensão da análise: Dez 2025 – Mar 2026")
    print("=" * 60)

    # 1. Ler CSVs
    print("\n1. A ler ficheiros CSV...")
    all_days = {}
    for csv_path in CSV_FILES:
        if not csv_path.exists():
            print(f"  ✗ Não encontrei: {csv_path.name}")
            sys.exit(1)
        d = parse_csv(csv_path)
        all_days.update(d)
    print(f"  Total de dias lidos: {len(all_days)}")

    # 2. Descarregar OMIE
    print("\n2. A descarregar preços OMIE...")
    omie = download_omie_range('2025-12-01', '2026-03-31')

    # 3. Descarregar temperatura
    print("\n3. A descarregar temperatura...")
    temp_by_date = fetch_temperature('2025-12-01', '2026-03-31')

    # 4. Calcular dados por mês
    print("\n4. A calcular dados por mês...")
    combined = {
        'monthly_list': [],
        'heatmap': {},
        'cons_data': {},
        'days_data': {},
        'temp_monthly': {},
        'temp_hourly': {},
        'temp_daily': {},
        'temp_monthly_avg_list': [],
    }

    for mk in ['2025-12', '2026-01', '2026-02', '2026-03']:
        print(f"  {mk} ({ACTUAL[mk]['name']})...")
        result = build_month_data(mk, all_days, omie, temp_by_date)
        combined['monthly_list'].append(result['monthly'])
        combined['heatmap'].update(result['heatmap'])
        combined['cons_data'].update(result['cons_data'])
        combined['days_data'].update(result['days_data'])
        combined['temp_monthly'].update(result['temp_monthly'])
        combined['temp_hourly'].update(result['temp_hourly'])
        combined['temp_daily'].update(result['temp_daily'])
        combined['temp_monthly_avg_list'].append(result['temp_monthly_avg'])

        m = result['monthly']
        save = m['paid'] - m['fam']
        print(f"    Consumo: {m['kwh']} kWh  |  Pago: €{m['paid']:.2f}  |  Família: €{m['fam']:.2f}  |  Poupança: €{save:.2f} ({save/m['paid']*100:.0f}%)")

    # Guardar JSON intermédiário
    with open(OUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(combined, f, ensure_ascii=False, indent=2)
    print(f"\n  JSON guardado: {OUT_PATH}")

    # Calcular totais combinados (para o update do hero)
    combined['totals'] = {
        'paid': round(sum(m['paid'] for m in combined['monthly_list']), 2),
        'fam':  round(sum(m['fam']  for m in combined['monthly_list']), 2),
        'ami':  round(sum(m['ami']  for m in combined['monthly_list']), 2),
        'kwh':  round(sum(m['kwh']  for m in combined['monthly_list'])),
    }

    # 5. Actualizar HTML
    print("\n5. A actualizar energia_comparacao.html...")
    update_html(combined)

    # Resumo final
    print("\n" + "=" * 60)
    print("RESUMO — 12 meses (Abr 2025 a Mar 2026)")
    print("=" * 60)
    html = HTML_PATH.read_text(encoding='utf-8')
    m = re.search(r'const DATA = (\{.*?\});', html, re.DOTALL)
    if m:
        data = json.loads(m.group(1))
        t = data['totals']
        save = t['paid'] - t['fam']
        print(f"  Total consumo:    {t['kwh']:,} kWh")
        print(f"  Total pago:       €{t['paid']:,.2f}")
        print(f"  Com Ibelectra Família: €{t['fam']:,.2f}")
        print(f"  Poupança total:   €{save:,.2f} ({save/t['paid']*100:.0f}%)")

if __name__ == '__main__':
    main()
