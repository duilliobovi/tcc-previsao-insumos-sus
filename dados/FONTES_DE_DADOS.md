# Fontes de dados

Os dados não fazem parte do repositório. Todos são públicos e devem ser obtidos nos portais oficiais
listados abaixo e gravados na pasta `DATASETS/`, na raiz do repositório, com os nomes indicados. Outra
pasta pode ser usada se a variável de ambiente `TCC_DADOS` apontar para ela.

Data de acesso utilizada no trabalho: 18 out. 2026.

## 1. SIA/SUS: demanda a prever (Python)

- **Portal:** DATASUS, TabNet, Produção Ambulatorial do SUS por local de atendimento
  (http://tabnet.datasus.gov.br/cgi/deftohtm.exe?sia/cnv/qauf.def).
- **Consulta:**
  - Linha: Ano/mês processamento;
  - Coluna: Unidade da Federação;
  - Conteúdo: Qtd.aprovada;
  - Períodos: todos os disponíveis;
  - Seleção: Grupo procedimento = 06 Medicamentos (primeiro arquivo) e 07 Órteses, próteses e materiais
    especiais (segundo arquivo);
  - Formato: Colunas separadas por ";".
- **Gravação:** copiar a tabela exibida, da linha de cabeçalho (`"Ano/mês processamento";"RO";...`) até o
  último mês, em arquivo de texto UTF-8:
  - `DATASETS/DS-03_sia_sus_agregado_tabnet/tabnet_grupos/tabnet_qauf__grupo_06_medicamentos__qtd_aprovada.csv`
  - `DATASETS/DS-03_sia_sus_agregado_tabnet/tabnet_grupos/tabnet_qauf__grupo_07_opm__qtd_aprovada.csv`

## 2. SIH/SUS: gasto hospitalar e evasão de internações (R)

- **Portal:** DATASUS, TabNet, Produção Hospitalar do SUS:
  - por local de internação: http://tabnet.datasus.gov.br/cgi/deftohtm.exe?sih/cnv/qiuf.def
  - por local de residência: http://tabnet.datasus.gov.br/cgi/deftohtm.exe?sih/cnv/qruf.def
- **Consulta:** Linha = Ano/mês processamento; Coluna = Unidade da Federação; períodos que incluam 2024;
  Formato = Colunas separadas por ";".
- **Gravação**, no mesmo formato da seção 1, em `DATASETS/DS-01_sih_sus_agregado_tabnet/tabnet/`:

| Arquivo | Portal | Conteúdo |
|---|---|---|
| `tabnet_qiuf__ano_mes_processamento__valor_total.csv` | qiuf (local de internação) | Valor total |
| `tabnet_qiuf__ano_mes_processamento__internacoes.csv` | qiuf (local de internação) | Internações |
| `tabnet_qruf__ano_mes_processamento__internacoes.csv` | qruf (local de residência) | Internações |

## 3. Banco de Preços em Saúde (BPS): preços, modalidade de compra e fornecedores (R)

Arquivos em `DATASETS/DS-05_bps_banco_de_precos_em_saude/`:

| Arquivo | URL |
|---|---|
| `BPS_2020.xlsx` | https://www.gov.br/saude/pt-br/acesso-a-informacao/banco-de-precos/bases-anuais-compiladas/registro-de-compras-compilado-ano-base-2020/@@download/file |
| `BPS_2021.xlsx` | https://www.gov.br/saude/pt-br/acesso-a-informacao/banco-de-precos/bases-anuais-compiladas/registro-de-compras-compilado-ano-base-2021/@@download/file |
| `BPS_2022.xlsx` | https://www.gov.br/saude/pt-br/acesso-a-informacao/banco-de-precos/bases-anuais-compiladas/registro-de-compras-compilado-ano-base-2022/@@download/file |
| `Bases compiladas - 2023 a 2024.xlsx` | https://www.gov.br/saude/pt-br/acesso-a-informacao/banco-de-precos/bases-anuais-compiladas/registro-de-compras-compilados-ano-base-2023-2024/@@download/file |
| `2021_csv.zip` a `2025_csv.zip` | https://s3.sa-east-1.amazonaws.com/ckan.saude.gov.br/BPS/csv/AAAA_csv.zip (AAAA = ano), listados em https://dadosabertos.saude.gov.br/dataset/bps |

Os CSV de 2021 a 2025 trazem apenas medicamentos e servem para classificar os códigos CATMAT das bases
anuais em medicamentos ou em materiais e equipamentos.

## 4. CNES: leitos (R)

- **Origem:** servidor de arquivos do DATASUS, ftp://ftp.datasus.gov.br/dissemin/publicos/CNES/200508_/Dados/LT/
- **Arquivos:** `LT{UF}2512.dbc` (competência dezembro de 2025) para as 27 UFs, por exemplo `LTSP2512.dbc`.
- **Gravação:** `DATASETS/DS-08_cnes_leitos/LT/`

## 5. IBGE: população e malha territorial (R)

Arquivos em `DATASETS/DS-11_ibge_estimativas_de_populacao/`:

| Arquivo | URL |
|---|---|
| `sidra_t_6579_n3_all_v_9324_p_all.json` | https://apisidra.ibge.gov.br/values/t/6579/n3/all/v/9324/p/all |
| `malha_uf_brasil_ibge.geojson` | https://servicodados.ibge.gov.br/api/v3/malhas/paises/BR?formato=application/vnd.geo%2Bjson&qualidade=minima&intrarregiao=UF |
