"""
Simulação histórica de custos energéticos
- Inverno crise: Dez 2021 – Mar 2022 (preços OMIE em máximos históricos)
- Inverno recente: Dez 2025 – Mar 2026

Usa o perfil de consumo real de Nov 2025 como base (mês mais alto disponível)
e escala para os meses de inverno típicos.

Corre na pasta Downloads/Portugas:
    python3 simular_inverno.py
"""

import urllib.request, json, csv, io, datetime, time, os

# ── Parâmetros Ibelectra Família ──────────────────────────────────────────────
# Derivados empiricamente dos dados reais Abr-Nov 2025:
#   custo_€kWh = FIXED_c + OMIE_FACTOR × omie_€MWh / 100
FIXED_c    = 10.17   # c€/kWh  (rede + impostos + certificados verdes + VAT, parte fixa)
OMIE_FACTOR = 0.1304  # c€/kWh por €/MWh OMIE  (encargo variável OMIE×1.1639×1.23 ÷ ~7.66)

# Tarifa fixa (actual_rate médio do utilizador nos dados reais)
FLAT_RATE_c = 24.9   # c€/kWh  (média Abr-Nov 2025: 24.3–25.5 c€/kWh)

# ── Perfil horário base (média CONS_DATA real do utilizador, Nov 2025 = mês mais alto) ──
# Wh médios por hora do dia em Nov 2025 (extraído de CONS_DATA)
HOURLY_PROFILE_NOV = [
    1171.2, 1099.7, 1107.8, 1102.6, 1373.2, 1580.4,
    1186.2, 1565.7, 1908.5, 1838.4, 1781.1, 1818.3,
    1930.6, 1976.6, 1732.2, 1652.7, 1810.8, 2429.3,
    3904.1, 3349.2, 2717.5, 2420.4, 1720.7, 1536.8
]  # Wh por hora (média de todos os dias de Nov 2025)
TOTAL_NOV_Wh = sum(HOURLY_PROFILE_NOV) * 30   # ~Nov total: ~1341 kWh
PROFILE_NORM = [h / sum(HOURLY_PROFILE_NOV) for h in HOURLY_PROFILE_NOV]  # fracções do dia

# Consumo mensal estimado para meses de inverno (kWh)
# Nov 2025 real: 1341 kWh → escalamos para inverno típico português
WINTER_KWH = {
    '2021-12': 1650, '2022-01': 1900, '2022-02': 1820, '2022-03': 1500,
    '2025-12': 1650, '2026-01': 1900, '2026-02': 1820, '2026-03': 1500,
}

PERIODS = [
    ('2021-12-01', '2022-03-31', 'Crise energética (Dez 21 – Mar 22)'),
    ('2025-12-01', '2026-03-31', 'Inverno recente (Dez 25 – Mar 26)'),
]

def omie_url(date_str):
    d = date_str.replace('-', '')
    return f"https://www.omie.es/en/file-download?parents[]=omiedatosftp&parents[]=marginalpdbcpt&elem=marginalpdbcpt_{d}.1"

def fetch_omie_day(date_str):
    """Devolve lista de 24 preços (€/MWh) para Portugal para um dia."""
    url = omie_url(date_str)
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read().decode('latin-1')
    except Exception as e:
        print(f"  ⚠ Erro a buscar {date_str}: {e}")
        return None

    prices = {}
    for line in raw.splitlines():
        parts = [p.strip().replace(',', '.') for p in line.split(';')]
        if len(parts) < 6:
            continue
        try:
            hour = int(parts[2])
            price = float(parts[5])
            if 1 <= hour <= 24:
                prices[hour] = price
        except:
            continue

    if len(prices) < 20:
        return None
    return [prices.get(h, None) for h in range(1, 25)]

def date_range(start, end):
    d = datetime.date.fromisoformat(start)
    e = datetime.date.fromisoformat(end)
    while d <= e:
        yield str(d)
        d += datetime.timedelta(days=1)

def month_key(date_str):
    return date_str[:7]

def simulate_period(start, end, label):
    print(f"\n{'='*60}")
    print(f"Período: {label}")
    print(f"{'='*60}")

    monthly = {}  # month_key → {omie_prices: [], kwh_consumed: float}

    for ds in date_range(start, end):
        mk = month_key(ds)
        if mk not in monthly:
            monthly[mk] = {'daily_omie': [], 'kwh': WINTER_KWH.get(mk, 1500)}

        print(f"  A buscar {ds}...", end=' ', flush=True)
        prices = fetch_omie_day(ds)
        if prices:
            monthly[mk]['daily_omie'].append(prices)
            print(f"✓  ({min(p for p in prices if p):.0f}–{max(prices):.0f} €/MWh)")
        else:
            print("⚠ sem dados, a usar média do mês")
        time.sleep(0.3)  # respeitar servidor

    results = []
    for mk in sorted(monthly.keys()):
        data = monthly[mk]
        days_data = data['daily_omie']
        monthly_kwh = data['kwh']
        n_days = len(days_data) if days_data else 28

        if not days_data:
            print(f"  ⚠ Sem dados OMIE para {mk}, a saltar")
            continue

        # Calcula custo por hora simulado:
        # consumo por hora = monthly_kwh × PROFILE_NORM[h] × (n_days / dias_no_mês_normal)
        # — distribuímos o consumo mensal pelos dias disponíveis proporcionalmente
        daily_kwh = monthly_kwh / n_days
        hourly_kwh = [daily_kwh * pct for pct in PROFILE_NORM]

        fam_total = 0.0
        flat_total = 0.0
        omie_hours = []

        for day_prices in days_data:
            for h, (omie_p, cons_kwh) in enumerate(zip(day_prices, hourly_kwh)):
                if omie_p is None:
                    omie_p = sum(p for p in day_prices if p is not None) / sum(1 for p in day_prices if p is not None)
                omie_hours.append(omie_p)
                fam_total  += cons_kwh * (FIXED_c + OMIE_FACTOR * omie_p) / 100
                flat_total += cons_kwh * FLAT_RATE_c / 100

        avg_omie = sum(omie_hours) / len(omie_hours)
        save = flat_total - fam_total
        save_pct = save / flat_total * 100

        print(f"\n  {mk}:")
        print(f"    OMIE médio:       {avg_omie:6.1f} €/MWh")
        print(f"    Consumo estimado: {monthly_kwh:5d} kWh")
        print(f"    Tarifário fixo:  €{flat_total:7.2f}")
        print(f"    Ibelectra Família:€{fam_total:7.2f}")
        if save > 0:
            print(f"    Poupança Família: €{save:7.2f}  ({save_pct:.0f}%)")
        else:
            print(f"    CUSTO EXTRA Família: €{abs(save):7.2f}  ({abs(save_pct):.0f}% mais caro!)")

        results.append({
            'month': mk,
            'avg_omie': round(avg_omie, 1),
            'kwh': monthly_kwh,
            'flat': round(flat_total, 2),
            'fam':  round(fam_total,  2),
            'save': round(save, 2),
            'save_pct': round(save_pct, 1),
            'n_days': n_days,
        })

    return results

# ── Main ──────────────────────────────────────────────────────────────────────
all_results = {}
for start, end, label in PERIODS:
    res = simulate_period(start, end, label)
    all_results[label] = res

# Grava JSON para incorporar no HTML
out_path = os.path.join(os.path.dirname(__file__), 'simulation_data.json')
with open(out_path, 'w', encoding='utf-8') as f:
    json.dump(all_results, f, ensure_ascii=False, indent=2)

print(f"\n\n✅ Resultado guardado em: {out_path}")
print("\nResumo final:")
for label, res in all_results.items():
    total_flat = sum(r['flat'] for r in res)
    total_fam  = sum(r['fam']  for r in res)
    total_save = total_flat - total_fam
    print(f"\n  {label}:")
    print(f"    4 meses fixo:    €{total_flat:.2f}")
    print(f"    4 meses Família: €{total_fam:.2f}")
    if total_save > 0:
        print(f"    Poupança total:  €{total_save:.2f} ({total_save/total_flat*100:.0f}%)")
    else:
        print(f"    EXTRA pago:     €{abs(total_save):.2f} ({abs(total_save)/total_flat*100:.0f}% mais caro!)")
