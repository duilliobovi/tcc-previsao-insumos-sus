"""Interface de linha de comando: monta a configuração e conduz as duas fases."""

import argparse
import logging
import re
import shutil
import sys
from collections import Counter, defaultdict
from collections.abc import Sequence
from pathlib import Path

from coletor import __version__
from coletor.baixador import Baixador
from coletor.cliente_ftp import ClienteFtp
from coletor.cliente_http import ClienteHttp, LimitadorPorHost
from coletor.config import (
    ARQUIVO_MD_PADRAO, CONEXOES_POR_HOST, CONEXOES_POR_HOST_PADRAO, DESTINO_PADRAO, FORMATOS_PADRAO,
    INTERVALO_PADRAO, INTERVALO_POR_HOST, LINHAS_TABNET_PADRAO, PREFIXOS_DATASUS, UFS, USER_AGENT,
    Configuracao,
)
from coletor.leitor_md import ler_secoes
from coletor.manifesto import Manifesto
from coletor.modelos import Competencia, Status
from coletor.planejamento import Plano, planejar, sondar_tamanhos
from coletor.resolvedores import Contexto, criar_resolvedores
from coletor.util import formatar_bytes

logger = logging.getLogger("coletor")

_MARGEM_DISCO = 256 << 20  # 256 MiB de folga para o sistema não ficar sem espaço


# ------------------------------------------------------------- argumentos
def _lista(texto: str) -> list[str]:
    return [item.strip() for item in texto.split(",") if item.strip()]


def _ufs(texto: str) -> frozenset[str]:
    if texto.strip().lower() == "todas":
        return frozenset(UFS)
    ufs = frozenset(uf.upper() for uf in _lista(texto))
    if invalidas := ufs - set(UFS):
        raise argparse.ArgumentTypeError(f"UF(s) inválida(s): {', '.join(sorted(invalidas))}")
    return ufs


def _prefixos(texto: str) -> frozenset[str]:
    prefixos = frozenset(p.upper() for p in _lista(texto))
    if invalidos := prefixos - set(PREFIXOS_DATASUS):
        raise argparse.ArgumentTypeError(f"tipo(s) inválido(s): {', '.join(sorted(invalidos))}")
    return prefixos


def _competencia(texto: str) -> Competencia:
    try:
        return Competencia.de_texto(texto)
    except ValueError as erro:
        raise argparse.ArgumentTypeError(str(erro)) from erro


def _regex(texto: str) -> re.Pattern[str]:
    try:
        return re.compile(texto, re.I)
    except re.error as erro:
        raise argparse.ArgumentTypeError(f"expressão regular inválida: {erro}") from erro


def _geocodigos(texto: str) -> tuple[int, ...]:
    try:
        return tuple(int(g) for g in _lista(texto))
    except ValueError as erro:
        raise argparse.ArgumentTypeError("geocódigos devem ser números IBGE de 7 dígitos") from erro


def _criar_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="baixar_datasets.py",
        description="Baixa os datasets listados em datasets_finais.md para o diretório DATASETS/.",
        epilog="Exemplo: %(prog)s --simular            (mostra o plano e o volume sem baixar)\n"
               "         %(prog)s --secoes 'DS-05|DS-11' (baixa só BPS e IBGE)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--arquivo-md", type=Path, default=ARQUIVO_MD_PADRAO,
                        help="Markdown com as URLs (padrão: %(default)s)")
    parser.add_argument("--destino", type=Path, default=DESTINO_PADRAO,
                        help="diretório de saída (padrão: %(default)s)")
    parser.add_argument("--ufs", type=_ufs, default=frozenset({"SP"}),
                        help="UFs dos microdados DATASUS, separadas por vírgula, ou 'todas' (padrão: SP)")
    parser.add_argument("--inicio", type=_competencia, default=Competencia(2008, 1),
                        help="primeira competência AAAA-MM (padrão: 2008-01)")
    parser.add_argument("--fim", type=_competencia, default=None,
                        help="última competência AAAA-MM (padrão: a mais recente disponível)")
    parser.add_argument("--tipos", dest="prefixos", type=_prefixos, default=frozenset(PREFIXOS_DATASUS),
                        help=f"tipos de .dbc do DATASUS (padrão: {','.join(PREFIXOS_DATASUS)})")
    parser.add_argument("--formatos", type=lambda t: frozenset(f.lower() for f in _lista(t)),
                        default=frozenset(FORMATOS_PADRAO),
                        help=f"formatos aceitos em portais com várias versões (padrão: {','.join(FORMATOS_PADRAO)})")
    parser.add_argument("--tabnet-linhas", type=lambda t: tuple(_lista(t)), default=LINHAS_TABNET_PADRAO,
                        help=f"linhas das tabelas TabNet (padrão: {','.join(LINHAS_TABNET_PADRAO)})")
    parser.add_argument("--geocodigos", type=_geocodigos, default=(),
                        help="municípios IBGE para o InfoDengue (padrão: capitais das UFs escolhidas)")
    parser.add_argument("--secoes", type=_regex, default=None,
                        help="regex para filtrar seções pelo código ou título (ex.: 'DS-0[25]')")
    parser.add_argument("--workers", type=int, default=6, help="downloads simultâneos (padrão: %(default)s)")
    parser.add_argument("--simular", action="store_true", help="só planeja: lista arquivos e volume")
    parser.add_argument("--forcar", action="store_true", help="baixa de novo mesmo os arquivos já presentes")
    parser.add_argument("--ignorar-robots", action="store_true",
                        help="não consulta robots.txt (use só com autorização do site)")
    parser.add_argument("-v", "--verbose", action="store_true", help="mostra cada arquivo planejado/baixado")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def _configuracao(args: argparse.Namespace) -> Configuracao:
    if args.fim is not None and args.fim < args.inicio:
        raise SystemExit(f"erro: --fim ({args.fim}) é anterior a --inicio ({args.inicio})")
    if args.workers < 1:
        raise SystemExit("erro: --workers deve ser ≥ 1")
    return Configuracao(
        arquivo_md=args.arquivo_md.resolve(),
        destino=args.destino.resolve(),
        ufs=args.ufs,
        inicio=args.inicio,
        fim=args.fim,
        prefixos=args.prefixos,
        formatos=args.formatos,
        linhas_tabnet=args.tabnet_linhas,
        geocodigos=args.geocodigos,
        secoes=args.secoes,
        workers=args.workers,
        simular=args.simular,
        forcar=args.forcar,
        respeitar_robots=not args.ignorar_robots,
    )


# ----------------------------------------------------------------- logging
def _configurar_logging(destino: Path, verbose: bool) -> Path:
    """Console resumido (stderr) e arquivo detalhado em ``DATASETS/coleta.log``."""
    destino.mkdir(parents=True, exist_ok=True)
    arquivo_log = destino / "coleta.log"
    console = logging.StreamHandler(sys.stderr)
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    console.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S"))
    arquivo = logging.FileHandler(arquivo_log, encoding="utf-8")
    arquivo.setLevel(logging.DEBUG)
    arquivo.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s [%(threadName)s] %(name)s: %(message)s"))
    raiz = logging.getLogger()
    raiz.setLevel(logging.DEBUG)
    raiz.handlers[:] = [console, arquivo]
    # As retentativas do urllib3 já aparecem resumidas no erro final de cada arquivo.
    for ruidoso in ("urllib3", "charset_normalizer", "chardet"):
        logging.getLogger(ruidoso).setLevel(logging.ERROR)
    return arquivo_log


# ----------------------------------------------------------------- relatórios
def _resumo_do_plano(plano: Plano, baixador: Baixador) -> None:
    contagem: Counter[str] = Counter()
    volume: defaultdict[str, int] = defaultdict(int)
    sem_tamanho: Counter[str] = Counter()
    for tarefa in plano.tarefas:
        pasta = tarefa.destino.parts[0]
        contagem[pasta] += 1
        if tarefa.tamanho_esperado is None:
            sem_tamanho[pasta] += 1
        else:
            volume[pasta] += tarefa.tamanho_esperado
        logger.debug("   plano: %s ← %s", tarefa.destino.as_posix(), tarefa.url)

    print("\nPlano de coleta", file=sys.stderr)
    print(f"{'pasta em DATASETS/':<52} {'arquivos':>8} {'volume conhecido':>17} {'sem tamanho':>11}",
          file=sys.stderr)
    for pasta in sorted(contagem):
        print(f"{pasta:<52} {contagem[pasta]:>8} {formatar_bytes(volume[pasta]):>17} {sem_tamanho[pasta]:>11}",
              file=sys.stderr)
    pendentes = baixador.bytes_pendentes(plano.tarefas)
    print(f"{'TOTAL':<52} {len(plano.tarefas):>8} {formatar_bytes(sum(volume.values())):>17} "
          f"{sum(sem_tamanho.values()):>11}", file=sys.stderr)
    print(f"Ainda a baixar (volume conhecido): {formatar_bytes(pendentes)}\n", file=sys.stderr)


def _verificar_espaco(destino: Path, necessario: int) -> bool:
    livre = shutil.disk_usage(destino).free
    if necessario + _MARGEM_DISCO > livre:
        logger.error(
            "Espaço insuficiente em %s: livre %s, necessário ≥ %s (+256 MiB de margem). "
            "Reduza o escopo com --ufs, --inicio/--fim, --tipos ou --secoes.",
            destino, formatar_bytes(livre), formatar_bytes(necessario),
        )
        return False
    return True


# ----------------------------------------------------------------- principal
def main(argv: Sequence[str] | None = None) -> int:
    args = _criar_parser().parse_args(argv)
    config = _configuracao(args)
    arquivo_log = _configurar_logging(config.destino, args.verbose)

    if not config.arquivo_md.is_file():
        logger.error("Arquivo não encontrado: %s", config.arquivo_md)
        return 2

    limitador = LimitadorPorHost(INTERVALO_PADRAO, INTERVALO_POR_HOST)
    http = ClienteHttp(user_agent=USER_AGENT, limitador=limitador, respeitar_robots=config.respeitar_robots)
    ftp = ClienteFtp()
    baixador = Baixador(
        destino=config.destino,
        transportes={"http": http, "https": http, "ftp": ftp},
        manifesto=Manifesto(config.destino / "MANIFEST.csv"),
        workers=config.workers,
        conexoes_por_host=CONEXOES_POR_HOST,
        conexoes_padrao=CONEXOES_POR_HOST_PADRAO,
        forcar=config.forcar,
    )

    try:
        secoes = ler_secoes(config.arquivo_md)
        logger.info("%d seções de dataset em %s", len(secoes), config.arquivo_md.name)
        plano = planejar(secoes, criar_resolvedores(Contexto(config, http, ftp)), config.secoes)
        logger.info("Consultando o tamanho dos arquivos fora das listagens FTP…")
        plano.tarefas = sondar_tamanhos(plano.tarefas, {"http": http, "https": http, "ftp": ftp}, config.workers)
        _resumo_do_plano(plano, baixador)

        if config.simular:
            logger.info("Simulação concluída; nada foi baixado. Log completo: %s", arquivo_log)
            return 1 if plano.falhas else 0
        if not _verificar_espaco(config.destino, baixador.bytes_pendentes(plano.tarefas)):
            return 3

        resultados = baixador.executar(plano.tarefas)
    except KeyboardInterrupt:
        logger.warning("Coleta interrompida. Rode de novo para retomar de onde parou.")
        return 130

    por_status = Counter(r.status for r in resultados)
    logger.info(
        "Concluído: %d baixados, %d já existiam, %d sem dados, %d falhas; "
        "%d URLs ignoradas, %d URLs com falha de resolução.",
        por_status[Status.BAIXADO], por_status[Status.EXISTENTE], por_status[Status.SEM_DADOS],
        por_status[Status.FALHA], len(plano.ignoradas), len(plano.falhas),
    )
    for resultado in (r for r in resultados if r.status is Status.FALHA):
        logger.error("  falha: %s — %s", resultado.tarefa.url, resultado.mensagem)
    for url, motivo in plano.falhas:
        logger.error("  não resolvida: %s — %s", url, motivo)
    logger.info("Manifesto: %s | Log: %s", config.destino / "MANIFEST.csv", arquivo_log)
    return 1 if por_status[Status.FALHA] or plano.falhas else 0
