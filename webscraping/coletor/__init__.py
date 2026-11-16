"""Coletor dos datasets do TCC listados em ``datasets_finais.md``.

O pacote lê o Markdown, descobre os arquivos de cada fonte (FTP do DATASUS,
portais CKAN, páginas Plone do gov.br, formulários do TabNet, APIs públicas)
e baixa tudo para ``DATASETS/``, registrando a proveniência em ``MANIFEST.csv``.
"""

__version__ = "1.0.0"
