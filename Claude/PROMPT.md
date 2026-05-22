# Dashboard de Comparação de Energia — Prompt

Cria um dashboard HTML single-file em português para comparar tarifários de electricidade em Portugal, usando os meus dados reais de consumo e preços OMIE do mercado diário.

**Tarifários a comparar:**
- Tarifário fixo actual (o que pago hoje, como referência)
- Ibelectra Família Simples
- Ibelectra Família Bi-horário Diário (Fora: 8h–22h todos os dias)
- Ibelectra Família Bi-horário Semanal (Fora: dias úteis 7h–24h; Sáb parcial verão/inverno; Dom+feriados todo Vazio)
- Cenários com solar fotovoltaico (simulação de autoconsumo)

Usa a fórmula ERSE: `(PMOMIE + CS) × (1 + Perd) + K`, com CS=0,009 €/kWh, Perd=16,39%, K=0,0035 €/kWh, IVA 23%, TAR separada para Vazio (1,58 c€/kWh) e Fora (8,35 c€/kWh).

**Secções do dashboard:**
1. Hero cards com poupança anual estimada por tarifário (vs fixo actual)
2. Gráfico de barras drilldown mensal → diário → hora a hora
3. Heatmap 24h × meses com vistas: preço OMIE, consumo, combinado, temperatura
4. Secção dedicada Vazio vs Fora com:
   - Toggle Ciclo (Diário / Semanal) + Vista (Σ Total kWh / ⌀ Média/dia / 📅 Semana)
   - Grelha hora × mês com intensidade = consumo real, laranja = Fora, verde = Vazio
   - Drill-down para vista diária dentro de cada mês
   - Vista semanal: média por dia da semana × hora, com 2 linhas de subtotais (Méd/dia e Total kWh)
   - Subtotais com kWh e % Fora/Vazio em todas as linhas e colunas
   - Banner de resumo do período completo

Escala de intensidade consistente entre Diário e Semanal (só muda a cor, não o brilho). Feriados portugueses incluídos. Design moderno com CSS variables, dark-border cards, responsivo. Todo o código numa única página HTML sem dependências externas.
