"""Exceções do domínio de coleta.

Separar as falhas *esperadas* (fonte fora do ar, página mudou de formato) das
falhas de programação permite que o orquestrador registre a primeira categoria
e siga com os demais datasets, sem mascarar bugs.
"""


class ErroColeta(Exception):
    """Falha esperada ao resolver ou baixar um dataset."""


class SemDados(ErroColeta):
    """A consulta é válida, mas não há registros (ex.: medida vazia no TabNet)."""


class BloqueadoPorRobots(ErroColeta):
    """O ``robots.txt`` do site proíbe acessar a URL com o nosso User-Agent."""


class UrlIgnorada(Exception):
    """A URL não aponta para dados baixáveis (documentação, portal, buscador).

    ``aviso`` distingue as URLs ignoradas de propósito (``False``) das que
    nenhuma estratégia reconheceu (``True``), que merecem atenção do usuário.
    """

    def __init__(self, motivo: str, *, aviso: bool = False) -> None:
        super().__init__(motivo)
        self.motivo = motivo
        self.aviso = aviso
