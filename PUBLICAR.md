# Como publicar este repositório no GitHub (envio manual pelo grupo)

O texto do TCC cita o endereço `https://github.com/duilliobovi/tcc-previsao-insumos-sus` e a etiqueta de versão
`plano-analise-final`. Para que a banca encontre exatamente o que o texto descreve:

## Opção A — linha de comando (preserva o histórico e a tag do plano)

```bash
# 1. No GitHub, crie o repositório vazio "tcc-previsao-insumos-sus" (público, sem README, sem licença).
# 2. Nesta pasta:
git remote add origin https://github.com/duilliobovi/tcc-previsao-insumos-sus.git
git push -u origin main
git push origin --tags          # envia plano-analise-final e versao-final
```

Confira em `https://github.com/duilliobovi/tcc-previsao-insumos-sus/tags` que as duas etiquetas aparecem e que a
data de `plano-analise-final` é anterior à de `versao-final`.

## Opção B — envio pela interface web

O envio de arquivos pela página do GitHub ("Add file › Upload files") **não leva o histórico nem as tags**, e a
prova de que o plano foi registrado antes do teste se perde. Se for o único caminho, envie também o arquivo
`historico_git.bundle` (gerado por `git bundle create historico_git.bundle --all`), que contém o histórico
completo e pode ser verificado com `git clone historico_git.bundle`.

## O que não deve ser enviado

- `DATASETS/` (dados brutos; já está no `.gitignore`): os dados são obtidos pelo coletor de `webscraping/`.
- Arquivos `__pycache__/` e `.Rhistory`.

Antes de enviar, rode `./reproduzir.sh` ou, ao menos, os testes:
`(cd python && python3 -m unittest discover -s tests -t .)`.
