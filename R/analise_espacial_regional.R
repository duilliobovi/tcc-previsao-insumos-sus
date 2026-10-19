#!/usr/bin/env Rscript
# =============================================================================
# Análise espacial exploratória dos gargalos regionais no abastecimento de
# medicamentos e materiais/equipamentos médico-hospitalares do SUS.
#
# Unidade de análise: as 27 Unidades da Federação (UF), agrupadas nas 5 regiões.
#
# Fontes (todas em <repositório>/DATASETS; ver dados/FONTES_DE_DADOS.md):
#   * BPS  — Banco de Preços em Saúde: compras públicas 2020–2024 (bases anuais
#            compiladas, com medicamentos e materiais) e CSV 2021–2025
#            (só medicamentos, usados para classificar os itens CATMAT);
#   * CNES — leitos por estabelecimento (LT{UF}2512.dbc, dez/2025);
#   * SIH/SUS (TabNet) — valor total e internações por UF, por local de
#            internação (qiuf) e por residência (qruf);
#   * IBGE — população estimada (SIDRA 6579) e malha das UFs (API de malhas).
#
# Indicadores de gargalo por UF (quanto maior, pior):
#   IPR_med / IPR_mat  índice de preço relativo: média geométrica de
#                      preço pago / mediana nacional do mesmo item no mesmo ano.
#                      Pagar mais pelo mesmo item sinaliza custo logístico,
#                      mercado fornecedor rarefeito ou baixa escala de compra;
#   pct_emergencial    % das compras por dispensa ou inexigibilidade de
#                      licitação, proxy de compra não planejada (ruptura);
#   hhi_fornecedores   concentração do valor comprado por fornecedor (HHI);
#   leitos_sus_10mil   leitos SUS por 10 mil hab. (sentido invertido);
#   gasto_sih_pc       gasto SIH por habitante (sentido invertido);
#   evasao_intern      internações de residentes / internações ocorridas na UF
#                      (> 1 indica dependência de outras UFs).
# O Índice de Gargalo Logístico (IGL) é a média dos z-escores orientados.
#
# Estatística espacial (implementada em R base, sem sf/spdep):
#   * matriz de vizinhança por contiguidade (rainha), padronizada na linha,
#     obtida dos vértices compartilhados na malha do IBGE;
#   * I de Moran global com teste de permutação (9 999 permutações);
#   * LISA (Anselin, 1995) com pseudo-p condicional e mapa de clusters.
#
# Uso:  Rscript R/analise_espacial_regional.R   (a partir da raiz do repositório)
# Saída: resultados/figuras/*.png e resultados/tabelas/*.csv
# =============================================================================

suppressPackageStartupMessages({
  library(data.table)
  library(jsonlite)
  library(readxl)
  library(read.dbc)
  library(ggplot2)
  library(patchwork)
  library(scales)
})

set.seed(2026)

# Números nos rótulos com vírgula decimal (padrão brasileiro).
sprintf_br <- function(...) gsub("(\\d)\\.(\\d)", "\\1,\\2", sprintf(...))

# ---- Caminhos ---------------------------------------------------------------
args_script <- commandArgs(trailingOnly = FALSE)
arquivo_script <- sub("^--file=", "", grep("^--file=", args_script, value = TRUE))
DIR_ANALISE <- if (length(arquivo_script)) dirname(normalizePath(arquivo_script)) else getwd()
DIR_REPO <- dirname(DIR_ANALISE)                       # o script fica em <repositório>/R
# Os dados não são versionados: $TCC_DADOS ou <repositório>/DATASETS (ver dados/FONTES_DE_DADOS.md).
DIR_DADOS <- Sys.getenv("TCC_DADOS", file.path(DIR_REPO, "DATASETS"))
DIR_FIG <- file.path(DIR_REPO, "resultados", "figuras")
DIR_TAB <- file.path(DIR_REPO, "resultados", "tabelas")
dir.create(DIR_FIG, recursive = TRUE, showWarnings = FALSE)
dir.create(DIR_TAB, recursive = TRUE, showWarnings = FALSE)

dir_bps <- file.path(DIR_DADOS, "DS-05_bps_banco_de_precos_em_saude")
dir_ibge <- file.path(DIR_DADOS, "DS-11_ibge_estimativas_de_populacao")
dir_cnes <- file.path(DIR_DADOS, "DS-08_cnes_leitos", "LT")
dir_sih <- file.path(DIR_DADOS, "DS-01_sih_sus_agregado_tabnet", "tabnet")

ANO_REFERENCIA <- 2024L  # último ano completo comum a BPS, SIH e IBGE

# ---- Tabela de UFs ----------------------------------------------------------
UFS <- data.table(
  cod = c(11:17, 21:29, 31:33, 35, 41:43, 50:53),
  sigla = c("RO", "AC", "AM", "RR", "PA", "AP", "TO",
            "MA", "PI", "CE", "RN", "PB", "PE", "AL", "SE", "BA",
            "MG", "ES", "RJ", "SP", "PR", "SC", "RS", "MS", "MT", "GO", "DF")
)
UFS[, regiao := factor(
  c("1" = "Norte", "2" = "Nordeste", "3" = "Sudeste", "4" = "Sul", "5" = "Centro-Oeste")[substr(cod, 1, 1)],
  levels = c("Norte", "Nordeste", "Centro-Oeste", "Sudeste", "Sul")
)]

CORES_REGIAO <- c(Norte = "#1b9e77", Nordeste = "#d95f02", `Centro-Oeste` = "#7570b3",
                  Sudeste = "#e7298a", Sul = "#66a61e")
FONTE_BPS <- "Fonte: elaborado pelos autores com dados do BPS/Ministério da Saúde, CNES, SIH/SUS e IBGE."

# =============================================================================
# 1. Malha das UFs (GeoJSON do IBGE) → polígonos para o ggplot2
# =============================================================================
malha <- fromJSON(file.path(dir_ibge, "malha_uf_brasil_ibge.geojson"), simplifyVector = FALSE)

# Cada feição vira um data.table de vértices com identificadores de polígono e
# anel, para que geom_polygon(subgroup = ...) desenhe os buracos (o DF é um
# buraco no polígono de Goiás).
vertices <- rbindlist(lapply(malha$features, function(feicao) {
  cod <- as.integer(feicao$properties$codarea)
  geom <- feicao$geometry
  poligonos <- if (geom$type == "Polygon") list(geom$coordinates) else geom$coordinates
  rbindlist(lapply(seq_along(poligonos), function(p) {
    rbindlist(lapply(seq_along(poligonos[[p]]), function(a) {
      m <- do.call(rbind, lapply(poligonos[[p]][[a]], unlist))
      data.table(cod = cod, poligono = p, anel = a, long = m[, 1], lat = m[, 2])
    }))
  }))
}))
vertices[, grupo := paste(cod, poligono, sep = "_")]

# Centroide pela fórmula do laço (shoelace) no maior anel externo de cada UF:
# serve para posicionar os rótulos.
centroide_anel <- function(x, y) {
  x2 <- c(x[-1], x[1]); y2 <- c(y[-1], y[1])
  a <- x * y2 - x2 * y
  area <- sum(a) / 2
  list(area = abs(area), cx = sum((x + x2) * a) / (6 * area), cy = sum((y + y2) * a) / (6 * area))
}
centroides <- vertices[anel == 1, centroide_anel(long, lat), by = .(cod, poligono)][
  order(-area)][, .SD[1], by = cod][, .(cod, cx, cy)]
centroides <- merge(centroides, UFS, by = "cod")

# Contiguidade "rainha": duas UFs são vizinhas se compartilham ao menos um
# vértice. As coordenadas são arredondadas para tolerar diferenças de 4ª casa.
chave_vertice <- unique(vertices[, .(cod, k = paste(round(long, 3), round(lat, 3)))])
pares <- merge(chave_vertice, chave_vertice, by = "k", allow.cartesian = TRUE)[cod.x < cod.y]
pares <- unique(pares[, .(cod.x, cod.y)])

n_uf <- nrow(UFS)
W <- matrix(0, n_uf, n_uf, dimnames = list(UFS$sigla, UFS$sigla))
idx <- function(cod) match(cod, UFS$cod)
W[cbind(idx(pares$cod.x), idx(pares$cod.y))] <- 1
W[cbind(idx(pares$cod.y), idx(pares$cod.x))] <- 1
stopifnot(all(rowSums(W) > 0))  # nenhuma UF isolada
Wp <- W / rowSums(W)            # padronização na linha

# Conferência de sanidade com vizinhanças conhecidas.
stopifnot(all(W["SP", c("MG", "RJ", "PR", "MS")] == 1), all(W["DF", c("GO", "MG")] == 1), sum(W["RS", ]) == 1)

# =============================================================================
# 2. População (IBGE/SIDRA 6579)
# =============================================================================
sidra <- fromJSON(file.path(dir_ibge, "sidra_t_6579_n3_all_v_9324_p_all.json"))[-1, ]
populacao <- data.table(cod = as.integer(sidra$D1C), ano = as.integer(sidra$D3C), pop = as.numeric(sidra$V))
pop_ref <- populacao[ano == ANO_REFERENCIA, .(cod, pop)]

# =============================================================================
# 3. BPS — compras públicas de medicamentos e materiais
# =============================================================================
# As bases anuais mudam o nome das colunas de um ano para outro; padroniza-se
# por expressão regular.
padronizar_bps <- function(df, ano) {
  nomes <- gsub("[\r\n ]+", " ", trimws(names(df)))
  pega <- function(padrao) {
    i <- grep(padrao, nomes, ignore.case = TRUE, perl = TRUE)
    if (length(i)) df[[i[1]]] else NA
  }
  cnpj_forn <- if (any(grepl("^CNPJ Fornecedor$", nomes, ignore.case = TRUE))) pega("^CNPJ Fornecedor$") else pega("^Fornecedor$")
  data.table(
    ano = ano,
    codigo = as.integer(gsub("\\D", "", pega("^C[oó]digo BR"))),
    uf = toupper(trimws(pega("^UF$"))),
    modalidade = pega("^Modalidade"),
    fornecedor = gsub("\\D", "", as.character(cnpj_forn)),
    qtd = suppressWarnings(as.numeric(pega("^(Qtd|Quantidade)"))),
    preco = suppressWarnings(as.numeric(pega("^(Pre[cç]o Unit|Unit[aá]rio)")))
  )
}

ler_xlsx <- function(arquivo, ano, aba = 1, pular = 0) {
  message("  lendo ", basename(arquivo), " [", aba, "]")
  padronizar_bps(read_excel(arquivo, sheet = aba, skip = pular, guess_max = 5000), ano)
}

message("BPS: lendo bases anuais…")
bps <- rbindlist(list(
  ler_xlsx(file.path(dir_bps, "BPS_2020.xlsx"), 2020L),
  ler_xlsx(file.path(dir_bps, "BPS_2021.xlsx"), 2021L),
  ler_xlsx(file.path(dir_bps, "BPS_2022.xlsx"), 2022L),
  ler_xlsx(file.path(dir_bps, "Bases compiladas - 2023 a 2024.xlsx"), 2023L, "2023", 1),
  ler_xlsx(file.path(dir_bps, "Bases compiladas - 2023 a 2024.xlsx"), 2024L, "2024", 1)
))
bps <- bps[uf %in% UFS$sigla & is.finite(preco) & preco > 0 & is.finite(qtd) & qtd > 0 & !is.na(codigo)]

# Os CSV do portal de dados abertos (2021–2025) trazem só "DROGAS E
# MEDICAMENTOS": o conjunto de códigos CATMAT deles separa medicamentos de
# materiais/equipamentos nas bases anuais.
message("BPS: lendo CSV de medicamentos…")
csv_med <- rbindlist(lapply(2021:2025, function(a) {
  fread(cmd = sprintf("unzip -p '%s'", file.path(dir_bps, sprintf("%d_csv.zip", a))),
        encoding = "UTF-8", select = c("ano_compra", "sg_uf", "co_catmat", "qt_medicamento",
                                       "vl_preco_unitario", "modalidade", "cnpj_fornecedor"))
}))
codigos_medicamento <- unique(csv_med$co_catmat)
bps[, tipo := fifelse(codigo %in% codigos_medicamento, "Medicamentos", "Materiais e equipamentos")]

# 2025 ainda não tem base anual compilada: entra só na série temporal, a partir
# do CSV (medicamentos).
bps_2025 <- csv_med[ano_compra == 2025 & sg_uf %in% UFS$sigla & vl_preco_unitario > 0 & qt_medicamento > 0,
                    .(ano = 2025L, codigo = co_catmat, uf = sg_uf, modalidade,
                      fornecedor = as.character(cnpj_fornecedor), qtd = qt_medicamento,
                      preco = vl_preco_unitario, tipo = "Medicamentos")]
bps_serie <- rbind(bps, bps_2025)

# Índice de preço relativo: só itens comprados em pelo menos 5 UFs no ano, para
# que a mediana nacional seja uma referência e não o preço de um único comprador.
precos_relativos <- function(d) {
  d <- copy(d)
  d[, n_ufs := uniqueN(uf), by = .(codigo, ano)]
  d <- d[n_ufs >= 5]
  d[, razao := preco / median(preco), by = .(codigo, ano)]
  d[razao >= 0.05 & razao <= 20]  # descarta erros de unidade de medida
}
pr <- precos_relativos(bps_serie)

ipr_uf <- dcast(
  pr[ano <= ANO_REFERENCIA, .(ipr = exp(mean(log(razao)))), by = .(uf, tipo)],
  uf ~ tipo, value.var = "ipr"
)
setnames(ipr_uf, c("Medicamentos", "Materiais e equipamentos"), c("ipr_med", "ipr_mat"))

compras_uf <- bps[, .(
  n_compras = .N,
  pct_emergencial = 100 * mean(grepl("dispensa|inexigibilidade", modalidade, ignore.case = TRUE)),
  valor = sum(qtd * preco)
), by = uf]
hhi_uf <- bps[, .(valor = sum(qtd * preco)), by = .(uf, fornecedor)][
  , .(hhi_fornecedores = 1e4 * sum((valor / sum(valor))^2)), by = uf]

# Série regional anual do índice de preço (2020–2025).
serie_regional <- merge(pr, UFS[, .(uf = sigla, regiao)], by = "uf")[
  , .(ipr = exp(mean(log(razao))), n = .N), by = .(regiao, ano, tipo)]

# =============================================================================
# 4. CNES — leitos SUS (dez/2025)
# =============================================================================
message("CNES: lendo leitos…")
leitos <- rbindlist(lapply(list.files(dir_cnes, "^LT[A-Z]{2}2512\\.dbc$", full.names = TRUE), function(f) {
  x <- as.data.table(read.dbc(f))
  x[, .(cod = as.integer(substr(as.character(CODUFMUN), 1, 2)), qt_sus = as.numeric(QT_SUS),
        qt_exist = as.numeric(QT_EXIST))]
}))[, .(leitos_sus = sum(qt_sus), leitos_exist = sum(qt_exist)), by = cod]

# =============================================================================
# 5. SIH/SUS (TabNet) — gasto e fluxo de internações
# =============================================================================
ler_tabnet_anual <- function(arquivo, ano) {
  if (!file.exists(arquivo)) return(NULL)
  linhas <- readLines(arquivo, encoding = "UTF-8")
  cab <- gsub('"', "", strsplit(linhas[1], ";")[[1]])
  alvo <- grep(sprintf('^"?%d"?;', ano), linhas, value = TRUE)
  if (!length(alvo)) return(NULL)
  valores <- gsub('"', "", strsplit(alvo[1], ";")[[1]])
  valores <- as.numeric(gsub(",", ".", gsub("\\.", "", sub("^-$", "0", valores))))
  data.table(sigla = cab[-1], valor = valores[-1])[sigla %in% UFS$sigla]
}
sih_valor <- ler_tabnet_anual(file.path(dir_sih, "tabnet_qiuf__ano_mes_processamento__valor_total.csv"), ANO_REFERENCIA)
int_local <- ler_tabnet_anual(file.path(dir_sih, "tabnet_qiuf__ano_mes_processamento__internacoes.csv"), ANO_REFERENCIA)
int_resid <- ler_tabnet_anual(file.path(dir_sih, "tabnet_qruf__ano_mes_processamento__internacoes.csv"), ANO_REFERENCIA)

# =============================================================================
# 6. Base por UF e Índice de Gargalo Logístico (IGL)
# =============================================================================
base <- copy(UFS)
base <- merge(base, pop_ref, by = "cod", all.x = TRUE)
base <- merge(base, ipr_uf, by.x = "sigla", by.y = "uf", all.x = TRUE)
base <- merge(base, compras_uf, by.x = "sigla", by.y = "uf", all.x = TRUE)
base <- merge(base, hhi_uf, by.x = "sigla", by.y = "uf", all.x = TRUE)
base <- merge(base, leitos, by = "cod", all.x = TRUE)
base <- merge(base, setnames(copy(sih_valor), "valor", "sih_valor"), by = "sigla", all.x = TRUE)
base[, leitos_sus_10mil := 1e4 * leitos_sus / pop]
base[, gasto_sih_pc := sih_valor / pop]
base[, compras_100mil := 1e5 * fcoalesce(as.numeric(n_compras), 0) / pop]
if (!is.null(int_local) && !is.null(int_resid)) {
  fluxo <- merge(int_resid, int_local, by = "sigla", suffixes = c("_resid", "_local"))
  base <- merge(base, fluxo[, .(sigla, evasao_intern = valor_resid / valor_local)], by = "sigla", all.x = TRUE)
}

# Sentido: +1 = quanto maior, pior; -1 = quanto maior, melhor.
COMPONENTES <- c(ipr_med = 1, ipr_mat = 1, pct_emergencial = 1, hhi_fornecedores = 1,
                 leitos_sus_10mil = -1, gasto_sih_pc = -1, evasao_intern = 1)
COMPONENTES <- COMPONENTES[names(COMPONENTES) %in% names(base)]
z <- function(x) (x - mean(x, na.rm = TRUE)) / sd(x, na.rm = TRUE)
for (v in names(COMPONENTES)) base[, (paste0("z_", v)) := COMPONENTES[[v]] * z(get(v))]
base[, igl := rowMeans(.SD, na.rm = TRUE), .SDcols = paste0("z_", names(COMPONENTES))]
setorder(base, cod)
stopifnot(identical(base$sigla, UFS$sigla))  # mesma ordem da matriz W

fwrite(base, file.path(DIR_TAB, "indicadores_gargalo_por_uf.csv"))

# =============================================================================
# 7. Estatística espacial: Moran global e LISA
# =============================================================================
moran_global <- function(x, W, n_perm = 9999) {
  zx <- x - mean(x)
  i_obs <- as.numeric(crossprod(zx, W %*% zx) / crossprod(zx))  # S0 = n com W padronizada
  i_perm <- replicate(n_perm, { zp <- sample(zx); as.numeric(crossprod(zp, W %*% zp) / crossprod(zp)) })
  # p unilateral (autocorrelação positiva, hipótese de agrupamento) e bilateral.
  list(I = i_obs, esperado = -1 / (length(x) - 1),
       p = (sum(i_perm >= i_obs) + 1) / (n_perm + 1),
       p_bilateral = min(1, 2 * (min(sum(i_perm >= i_obs), sum(i_perm <= i_obs)) + 1) / (n_perm + 1)),
       perm = i_perm)
}

lisa <- function(x, W, n_perm = 9999, alfa = 0.05) {
  zx <- (x - mean(x)) / sd(x)
  lag <- as.numeric(W %*% zx)
  ii <- zx * lag
  # Permutação condicional: o valor de i fica fixo e os vizinhos são sorteados
  # entre as demais UFs (Anselin, 1995).
  p <- vapply(seq_along(zx), function(i) {
    w_i <- W[i, -i]; outros <- zx[-i]; viz <- w_i > 0
    sim <- replicate(n_perm, zx[i] * sum(w_i[viz] * sample(outros, sum(viz))))
    (min(sum(sim >= ii[i]), sum(sim <= ii[i])) + 1) / (n_perm + 1)
  }, numeric(1))
  quadrante <- fifelse(zx >= 0 & lag >= 0, "Alto-Alto",
               fifelse(zx < 0 & lag < 0, "Baixo-Baixo",
               fifelse(zx >= 0, "Alto-Baixo", "Baixo-Alto")))
  data.table(z = zx, lag = lag, Ii = ii, p = p,
             cluster = fifelse(p <= alfa, quadrante, "Não significativo"))
}

# Variáveis com UFs sem dado (lacunas do BPS): o Moran é calculado no subgrafo
# das UFs observadas, com W binária reduzida e novamente padronizada na linha.
moran_disponivel <- function(x, W_bin, n_perm = 9999) {
  ok <- !is.na(x)
  Wr <- W_bin[ok, ok]
  ok2 <- rowSums(Wr) > 0                       # descarta UFs que ficaram sem vizinho
  Wr <- Wr[ok2, ok2]
  res <- moran_global(x[ok][ok2], Wr / rowSums(Wr), n_perm)
  res$n <- sum(ok2)
  res
}

mg <- moran_disponivel(base$igl, W)
mg_ipr_med <- moran_disponivel(base$ipr_med, W)
mg_ipr_mat <- moran_disponivel(base$ipr_mat, W)
lisa_igl <- cbind(base[, .(cod, sigla, regiao, igl)], lisa(base$igl, Wp))

resumo_moran <- data.table(
  variavel = c("IGL", "IPR medicamentos", "IPR materiais e equipamentos"),
  I_moran = c(mg$I, mg_ipr_med$I, mg_ipr_mat$I),
  E_I = c(mg$esperado, mg_ipr_med$esperado, mg_ipr_mat$esperado),
  n_ufs = c(mg$n, mg_ipr_med$n, mg_ipr_mat$n),
  p_permutacao = c(mg$p, mg_ipr_med$p, mg_ipr_mat$p),
  p_bilateral = c(mg$p_bilateral, mg_ipr_med$p_bilateral, mg_ipr_mat$p_bilateral)
)
fwrite(resumo_moran, file.path(DIR_TAB, "moran_global.csv"))
fwrite(lisa_igl, file.path(DIR_TAB, "lisa_igl.csv"))

kw <- kruskal.test(igl ~ regiao, data = base)
fwrite(data.table(teste = "Kruskal-Wallis IGL ~ região", estatistica = kw$statistic,
                  gl = kw$parameter, p = kw$p.value), file.path(DIR_TAB, "kruskal_wallis_regioes.csv"))

regioes <- base[, .(igl_medio = mean(igl), ipr_med = mean(ipr_med, na.rm = TRUE),
                    ipr_mat = mean(ipr_mat, na.rm = TRUE), pct_emergencial = mean(pct_emergencial, na.rm = TRUE),
                    leitos_sus_10mil = 1e4 * sum(leitos_sus) / sum(pop), gasto_sih_pc = sum(sih_valor) / sum(pop)),
                by = regiao][order(-igl_medio)]
fwrite(regioes, file.path(DIR_TAB, "resumo_por_regiao.csv"))

# =============================================================================
# 8. Gráficos
# =============================================================================
tema_mapa <- theme_void(base_size = 10) +
  theme(legend.position = "right", plot.title = element_text(face = "bold", size = 11),
        plot.caption = element_text(hjust = 0, size = 7, colour = "grey30"),
        plot.background = element_rect(fill = "white", colour = NA))

mapa_base <- function(dados, variavel, titulo, legenda, paleta = "rocket", direcao = -1, rotulos = TRUE) {
  d <- merge(vertices, dados[, c("cod", variavel), with = FALSE], by = "cod")
  g <- ggplot(d, aes(long, lat)) +
    geom_polygon(aes(group = grupo, subgroup = anel, fill = .data[[variavel]]), colour = "white", linewidth = 0.25) +
    coord_quickmap() +
    scale_fill_viridis_c(option = paleta, direction = direcao, name = legenda) +
    labs(title = titulo) + tema_mapa
  if (rotulos) g <- g + geom_text(data = centroides, aes(cx, cy, label = sigla), size = 2.3, colour = "grey10")
  g
}

salvar <- function(grafico, nome, largura = 16, altura = 12) {
  # No TCC, título e fonte ficam no documento (ABNT); a imagem guarda só o subtítulo com as estatísticas.
  if (Sys.getenv("FIGURAS_SEM_TITULO", "1") == "1") {
    grafico <- if (inherits(grafico, "patchwork")) grafico + plot_annotation(title = NULL, caption = NULL)
         else grafico + labs(title = NULL, caption = NULL)
  }
  ggsave(file.path(DIR_FIG, nome), grafico, width = largura, height = altura, units = "cm", dpi = 300, bg = "white")
}

# Fig. 1 — mapa do IGL.
salvar(
  mapa_base(base, "igl", "Índice de Gargalo Logístico (IGL) por UF", "IGL\n(z médio)", "magma") +
    labs(subtitle = "Compras do BPS 2020–2024, leitos CNES dez/2025, SIH/SUS 2024",
         caption = FONTE_BPS),
  "fig01_mapa_igl.png"
)

# Fig. 2 — painel com os componentes.
rotulo_comp <- c(ipr_med = "Preço relativo — medicamentos", ipr_mat = "Preço relativo — materiais/equip.",
                 pct_emergencial = "% compras emergenciais", hhi_fornecedores = "Concentração de fornecedores (HHI)",
                 leitos_sus_10mil = "Leitos SUS / 10 mil hab.", gasto_sih_pc = "Gasto SIH por hab. (R$)",
                 evasao_intern = "Internações de residentes / ocorridas")
paineis <- lapply(names(COMPONENTES), function(v) {
  mapa_base(base, v, rotulo_comp[[v]], NULL, if (COMPONENTES[[v]] > 0) "inferno" else "viridis",
            direcao = if (COMPONENTES[[v]] > 0) -1 else 1, rotulos = FALSE) +
    theme(plot.title = element_text(size = 8, face = "bold"), legend.key.width = unit(0.25, "cm"),
          legend.text = element_text(size = 6))
})
salvar(wrap_plots(paineis, ncol = 3) + plot_annotation(
  title = "Componentes do IGL por UF", caption = FONTE_BPS,
  theme = theme(plot.title = element_text(face = "bold"), plot.caption = element_text(hjust = 0, size = 7))),
  "fig02_painel_componentes.png", 18, 16)

# Fig. 3 — diagrama de espalhamento de Moran.
salvar(
  ggplot(lisa_igl, aes(z, lag)) +
    geom_hline(yintercept = 0, colour = "grey60") + geom_vline(xintercept = 0, colour = "grey60") +
    geom_smooth(method = "lm", formula = y ~ x, se = FALSE, colour = "black", linewidth = 0.5) +
    geom_point(aes(colour = regiao), size = 2.5) +
    geom_text(aes(label = sigla), size = 2.4, vjust = -0.9) +
    scale_colour_manual(values = CORES_REGIAO, name = "Região") +
    labs(title = "Diagrama de espalhamento de Moran — IGL",
         subtitle = sprintf_br("I de Moran = %.3f  (E[I] = %.3f; pseudo-p = %.4f; 9 999 permutações)", mg$I, mg$esperado, mg$p),
         x = "IGL padronizado da UF", y = "Média do IGL padronizado dos vizinhos (defasagem espacial)",
         caption = FONTE_BPS) +
    theme_minimal(base_size = 10) + theme(plot.title = element_text(face = "bold"), plot.caption = element_text(hjust = 0, size = 7)),
  "fig03_moran_igl.png"
)

# Fig. 4 — mapa de clusters LISA.
cores_lisa <- c(`Alto-Alto` = "#b2182b", `Baixo-Baixo` = "#2166ac", `Alto-Baixo` = "#ef8a62",
                `Baixo-Alto` = "#67a9cf", `Não significativo` = "grey88")
d_lisa <- merge(vertices, lisa_igl[, .(cod, cluster)], by = "cod")
salvar(
  ggplot(d_lisa, aes(long, lat)) +
    geom_polygon(aes(group = grupo, subgroup = anel, fill = cluster), colour = "white", linewidth = 0.25) +
    geom_text(data = merge(centroides, lisa_igl[, .(cod, p)], by = "cod"),
              aes(cx, cy, label = sigla), size = 2.3) +
    coord_quickmap() +
    scale_fill_manual(values = cores_lisa, name = "Cluster LISA\n(p ≤ 0,05)", drop = FALSE) +
    labs(title = "Agrupamentos locais do IGL (LISA)",
         subtitle = "Alto-Alto: UF com gargalo elevado cercada de UFs também com gargalo elevado",
         caption = FONTE_BPS) + tema_mapa,
  "fig04_lisa_igl.png"
)

# Fig. 5 — IGL por região.
salvar(
  ggplot(base, aes(reorder(regiao, -igl, median), igl, fill = regiao)) +
    geom_hline(yintercept = 0, linetype = 2, colour = "grey50") +
    geom_boxplot(alpha = 0.6, outlier.shape = NA, width = 0.6) +
    geom_jitter(width = 0.12, size = 1.8) +
    geom_text(aes(label = sigla), size = 2.4, nudge_x = 0.25) +
    scale_fill_manual(values = CORES_REGIAO, guide = "none") +
    labs(title = "Distribuição do IGL por região",
         subtitle = sprintf_br("Kruskal-Wallis: H = %.2f, gl = %d, p = %.4f", kw$statistic, kw$parameter, kw$p.value),
         x = NULL, y = "IGL (média dos z-escores orientados)", caption = FONTE_BPS) +
    theme_minimal(base_size = 10) + theme(plot.title = element_text(face = "bold"), plot.caption = element_text(hjust = 0, size = 7)),
  "fig05_boxplot_regioes.png"
)

# Fig. 6 — série regional do preço relativo.
salvar(
  ggplot(serie_regional[n >= 30], aes(ano, ipr, colour = regiao)) +
    geom_hline(yintercept = 1, linetype = 2, colour = "grey50") +
    geom_line(linewidth = 0.8) + geom_point(size = 1.6) +
    facet_wrap(~tipo) +
    scale_colour_manual(values = CORES_REGIAO, name = "Região") +
    scale_y_continuous(labels = label_number(accuracy = 0.01, decimal.mark = ",")) +
    labs(title = "Índice de preço relativo por região e ano",
         subtitle = "1,00 = mediana nacional do item no ano; pontos com ≥ 30 compras; 2025 só medicamentos",
         x = NULL, y = "Preço pago / mediana nacional (média geométrica)", caption = FONTE_BPS) +
    theme_minimal(base_size = 10) + theme(legend.position = "bottom", plot.title = element_text(face = "bold"),
                                          plot.caption = element_text(hjust = 0, size = 7)),
  "fig06_serie_ipr_regioes.png", 16, 10
)

# Fig. 7 — cobertura do BPS (registros por 100 mil hab.): UFs em cinza não informaram compras.
salvar(
  mapa_base(base[, .(cod, cobertura = fifelse(compras_100mil > 0, compras_100mil, NA_real_))], "cobertura",
            "Registros de compras no BPS por 100 mil habitantes (2020–2024)", "Registros /\n100 mil hab.", "mako") +
    labs(subtitle = "Cinza: UF sem compras registradas nas bases anuais compiladas", caption = FONTE_BPS),
  "fig07_cobertura_bps.png"
)

# Fig. 8 — dificuldade de previsão por UF (MASE do melhor modelo de ML no teste, média dos
# grupos 06 e 07 e dos horizontes), com o Moran global da previsibilidade.
arq_mase <- file.path(DIR_TAB, "mase_por_uf.csv")
if (file.exists(arq_mase)) {
  mase_uf <- fread(arq_mase)[familia == "ML*", .(mase = mean(mase)), by = .(sigla = uf)]
  base <- merge(base, mase_uf, by = "sigla", all.x = TRUE)
  setorder(base, cod)
  mg_mase <- moran_disponivel(base$mase, W)
  resumo_moran <- rbind(resumo_moran, data.table(variavel = "MASE do ML* (dificuldade de previsão)",
                                                 I_moran = mg_mase$I, E_I = mg_mase$esperado, n_ufs = mg_mase$n,
                                                 p_permutacao = mg_mase$p, p_bilateral = mg_mase$p_bilateral))
  fwrite(resumo_moran, file.path(DIR_TAB, "moran_global.csv"))
  fwrite(base, file.path(DIR_TAB, "indicadores_gargalo_por_uf.csv"))
  cor_igl_mase <- cor.test(base$igl, base$mase, method = "spearman", exact = FALSE)
  fwrite(data.table(teste = "Spearman IGL × MASE", rho = cor_igl_mase$estimate, p = cor_igl_mase$p.value),
         file.path(DIR_TAB, "correlacao_igl_mase.csv"))
  salvar(
    mapa_base(base, "mase", "Dificuldade de previsão da demanda de insumos por UF", "MASE\n(ML*)", "plasma") +
      labs(subtitle = sprintf_br("I de Moran = %.3f (pseudo-p = %.4f); Spearman com o IGL: ρ = %.2f (p = %.3f)",
                              mg_mase$I, mg_mase$p, cor_igl_mase$estimate, cor_igl_mase$p.value),
           caption = "Fonte: elaborado pelos autores com dados do SIA/SUS (TabNet), grupos 06 e 07, teste 2023–2025."),
    "fig08_mapa_mase_uf.png"
  )
}

# =============================================================================
# 9. Resumo no console
# =============================================================================
cat("\nCompras BPS usadas (2020–2024):", format(nrow(bps), big.mark = ".", decimal.mark = ","), "\n")
print(bps[, .N, by = tipo])
cat("\nMoran global:\n"); print(resumo_moran)
cat("\nKruskal-Wallis:", sprintf("H = %.2f, p = %.4f", kw$statistic, kw$p.value), "\n")
cat("\nPor região:\n"); print(regioes)
cat("\nUFs (ordem decrescente de IGL):\n")
print(base[order(-igl), .(sigla, regiao, igl = round(igl, 2), ipr_med = round(ipr_med, 2), ipr_mat = round(ipr_mat, 2),
                          pct_emergencial = round(pct_emergencial, 1), hhi = round(hhi_fornecedores),
                          leitos_sus_10mil = round(leitos_sus_10mil, 2), gasto_sih_pc = round(gasto_sih_pc, 1),
                          evasao = if ("evasao_intern" %in% names(base)) round(evasao_intern, 3) else NA)])
cat("\nLISA significativos:\n"); print(lisa_igl[cluster != "Não significativo", .(sigla, cluster, Ii = round(Ii, 2), p)])
