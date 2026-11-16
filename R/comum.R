# =============================================================================
# Funções e padrões comuns aos scripts R do repositório.
#
#   * caminhos (a partir da localização do script, com espaços no caminho);
#   * tema gráfico único do trabalho e paletas acessíveis (Okabe-Ito);
#   * malha das UFs (GeoJSON do IBGE) em polígonos para o ggplot2;
#   * matriz de vizinhança por contiguidade (rainha), I de Moran e LISA com
#     testes de permutação, escritos em R base (sem sf/spdep).
#
# Uso: source("R/comum.R") no início de cada script (a partir da raiz).
# As figuras saem SEM título e sem fonte dentro da imagem: título, fonte e notas
# ficam no texto, abaixo e acima da figura, como pede a ABNT e o orientador.
# =============================================================================

suppressPackageStartupMessages({
  library(data.table)
  library(jsonlite)
  library(ggplot2)
  library(patchwork)
  library(scales)
})

local({
  args_script <- commandArgs(trailingOnly = FALSE)
  arquivo <- gsub("~+~", " ", sub("^--file=", "", grep("^--file=", args_script, value = TRUE)), fixed = TRUE)
  dir_r <- if (length(arquivo)) dirname(normalizePath(arquivo)) else file.path(getwd(), "R")
  DIR_REPO <<- dirname(dir_r)
})
DIR_DADOS <- Sys.getenv("TCC_DADOS", file.path(DIR_REPO, "DATASETS"))
DIR_TAB <- file.path(DIR_REPO, "resultados", "tabelas")
# FIGURAS_SLIDE=1 gera versões para os slides (fonte maior, pasta resultados/figuras/slides).
SLIDE <- identical(Sys.getenv("FIGURAS_SLIDE"), "1")
DIR_FIG <- file.path(DIR_REPO, "resultados", "figuras", if (SLIDE) "slides" else "")
dir.create(DIR_FIG, recursive = TRUE, showWarnings = FALSE)

ler <- function(nome) fread(file.path(DIR_TAB, nome), encoding = "UTF-8")

# ---- Números no padrão brasileiro ---------------------------------------------
num_br <- function(x, casas = 2) formatC(x, format = "f", digits = casas, big.mark = ".", decimal.mark = ",")
rotulo_br <- function(casas = 2) function(x) num_br(x, casas)
pct_br <- function(casas = 0) function(x) paste0(num_br(100 * x, casas), "%")

# ---- Tema e paletas -------------------------------------------------------------
CORES_FAMILIA <- c("Estatístico" = "#0072B2", "ML global" = "#D55E00", "ML local" = "#E69F00")
CORES_REGIAO <- c(Norte = "#009E73", Nordeste = "#E69F00", `Centro-Oeste` = "#CC79A7",
                  Sudeste = "#0072B2", Sul = "#56B4E9")
ROTULO_GRUPO <- c("06_medicamentos" = "Medicamentos (grupo 06)", "07_opm" = "OPM (grupo 07)")

tema_tcc <- function(base = 10) {
  theme_minimal(base_size = base) +
    theme(
      text = element_text(colour = "grey15"),
      axis.text = element_text(colour = "grey25"),
      axis.title = element_text(colour = "grey15"),
      panel.grid.minor = element_blank(),
      panel.grid.major = element_line(colour = "grey90", linewidth = 0.3),
      strip.text = element_text(face = "bold", hjust = 0, size = rel(1)),
      legend.position = "bottom",
      legend.title = element_text(size = rel(0.95)),
      legend.key.height = unit(0.4, "cm"),
      plot.margin = margin(6, 8, 6, 6)
    )
}
theme_set(tema_tcc(if (SLIDE) 13 else 10))

salvar <- function(grafico, nome, largura = 16, altura = 9) {
  if (SLIDE) altura <- altura * 1.15
  ggsave(file.path(DIR_FIG, nome), grafico, width = largura, height = altura, units = "cm", dpi = 300, bg = "white")
  invisible(file.path(DIR_FIG, nome))
}

# ---- UFs, malha e vizinhança ----------------------------------------------------
UFS <- data.table(
  cod = c(11:17, 21:29, 31:33, 35, 41:43, 50:53),
  sigla = c("RO", "AC", "AM", "RR", "PA", "AP", "TO", "MA", "PI", "CE", "RN", "PB", "PE", "AL", "SE", "BA",
            "MG", "ES", "RJ", "SP", "PR", "SC", "RS", "MS", "MT", "GO", "DF"))
UFS[, regiao := factor(c("1" = "Norte", "2" = "Nordeste", "3" = "Sudeste", "4" = "Sul", "5" = "Centro-Oeste")[
  substr(cod, 1, 1)], levels = c("Norte", "Nordeste", "Centro-Oeste", "Sudeste", "Sul"))]

carregar_malha <- function() {
  malha <- fromJSON(file.path(DIR_DADOS, "DS-11_ibge_estimativas_de_populacao", "malha_uf_brasil_ibge.geojson"),
                    simplifyVector = FALSE)
  # Cada feição vira vértices com polígono e anel, para geom_polygon(subgroup = ) desenhar buracos (DF em GO).
  v <- rbindlist(lapply(malha$features, function(f) {
    cod <- as.integer(f$properties$codarea)
    pol <- if (f$geometry$type == "Polygon") list(f$geometry$coordinates) else f$geometry$coordinates
    rbindlist(lapply(seq_along(pol), function(p) rbindlist(lapply(seq_along(pol[[p]]), function(a) {
      m <- do.call(rbind, lapply(pol[[p]][[a]], unlist))
      data.table(cod = cod, poligono = p, anel = a, long = m[, 1], lat = m[, 2])
    }))))
  }))
  v[, grupo := paste(cod, poligono, sep = "_")]
  merge(v, UFS, by = "cod")
}

centroides_ufs <- function(vertices) {
  laco <- function(x, y) {  # fórmula do laço (shoelace)
    x2 <- c(x[-1], x[1]); y2 <- c(y[-1], y[1]); a <- x * y2 - x2 * y; area <- sum(a) / 2
    list(area = abs(area), cx = sum((x + x2) * a) / (6 * area), cy = sum((y + y2) * a) / (6 * area))
  }
  c <- vertices[anel == 1, laco(long, lat), by = .(cod, poligono)][order(-area)][, .SD[1], by = cod]
  merge(c[, .(cod, cx, cy)], UFS, by = "cod")
}

matriz_vizinhanca <- function(vertices) {
  # Rainha: vizinhas se compartilham ao menos um vértice (coordenadas arredondadas na 3ª casa).
  k <- unique(vertices[, .(cod, k = paste(round(long, 3), round(lat, 3)))])
  pares <- unique(merge(k, k, by = "k", allow.cartesian = TRUE)[cod.x < cod.y, .(cod.x, cod.y)])
  W <- matrix(0, nrow(UFS), nrow(UFS), dimnames = list(UFS$sigla, UFS$sigla))
  i <- function(cod) match(cod, UFS$cod)
  W[cbind(i(pares$cod.x), i(pares$cod.y))] <- 1
  W[cbind(i(pares$cod.y), i(pares$cod.x))] <- 1
  stopifnot(all(rowSums(W) > 0), all(W["SP", c("MG", "RJ", "PR", "MS")] == 1), all(W["DF", c("GO", "MG")] == 1),
            sum(W["RS", ]) == 1)  # conferência com fronteiras conhecidas
  W
}

# ---- Estatística espacial (Moran, 1950; Anselin, 1995) -----------------------------
moran_global <- function(x, W, n_perm = 9999) {
  Wp <- W / rowSums(W)
  zx <- x - mean(x)
  estat <- function(z) as.numeric(crossprod(z, Wp %*% z) / crossprod(z))
  i_obs <- estat(zx)
  i_perm <- replicate(n_perm, estat(sample(zx)))
  list(I = i_obs, esperado = -1 / (length(x) - 1),
       p_bilateral = min(1, 2 * (min(sum(i_perm >= i_obs), sum(i_perm <= i_obs)) + 1) / (n_perm + 1)))
}

lisa <- function(x, W, n_perm = 9999) {
  # Permutação condicional: o valor da UF fica fixo e os vizinhos são sorteados entre as demais.
  Wp <- W / rowSums(W)
  zx <- (x - mean(x)) / sd(x)
  lag <- as.numeric(Wp %*% zx)
  ii <- zx * lag
  p <- vapply(seq_along(zx), function(i) {
    w <- Wp[i, -i]; viz <- w > 0; outros <- zx[-i]
    sim <- replicate(n_perm, zx[i] * sum(w[viz] * sample(outros, sum(viz))))
    (min(sum(sim >= ii[i]), sum(sim <= ii[i])) + 1) / (n_perm + 1)
  }, numeric(1))
  data.table(sigla = rownames(W), z = zx, lag = lag, Ii = ii, p = p,
             quadrante = fifelse(zx >= 0 & lag >= 0, "Alto-Alto", fifelse(zx < 0 & lag < 0, "Baixo-Baixo",
                                 fifelse(zx >= 0, "Alto-Baixo", "Baixo-Alto"))))
}
