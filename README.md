# PDF2OFX

Conversor de extratos bancários em PDF para OFX, publicado como site no Vercel: um frontend estático (`index.html`) e uma função serverless em Python (`api/index.py`) que reaproveitam o mesmo núcleo de extração, `converter_core.py`.

## O que o conversor faz

Permite enviar um ou vários PDFs, extrair texto de PDFs pesquisáveis, detectar automaticamente alguns bancos brasileiros, revisar e corrigir data, histórico e valor em uma tabela no navegador, e baixar o OFX 1.03 resultante.

A conversão depende da qualidade do texto extraído do PDF. PDFs que são apenas imagens, estão protegidos por senha ou possuem um layout não mapeado podem não gerar transações. O site avisa quando não encontra transações, evitando gerar um OFX silenciosamente incorreto.

## Alertas de sobreposição e qualidade do PDF

Antes de interpretar as transações, a extração verifica a geometria do PDF. Se caixas de texto ocuparem a mesma área, aparece um alerta de **sobreposição** com a página e os elementos envolvidos. Também é sinalizada página com imagem e pouco texto pesquisável, pois esse cenário pode indicar um extrato escaneado ou uma camada de texto incompleta.

Esses alertas não afirmam que cada sobreposição é necessariamente um erro visual; eles indicam que a ordem, a coluna ou o valor pode ter sido alterado na extração. Nesses casos, compare as linhas com a imagem original do extrato e corrija os campos na tabela antes de baixar o OFX. Documentos com alertas devem ser tratados como **revisão obrigatória**.

## Como funciona

1. O navegador envia o PDF para `POST /api/parse`, que extrai as transações e devolve JSON.
2. O usuário revisa/edita a tabela na própria página.
3. Ao clicar em **Gerar e baixar OFX**, o navegador envia os dados revisados para `POST /api/generate`, que monta o OFX e devolve o conteúdo para download — sem reprocessar o PDF.

Os PDFs enviados são gravados em um arquivo temporário durante a extração e apagados logo em seguida; nada fica armazenado no servidor.

## Como rodar localmente

```bash
npm i -g vercel   # se ainda não tiver a CLI
vercel dev
```

Isso sobe o `index.html` e a função `api/index.py` juntos em `http://localhost:3000`, replicando o ambiente do Vercel (inclusive as rotas definidas em `vercel.json`).

Alternativa sem a CLI do Vercel — roda só a função Python, para testar as rotas `/api/*` diretamente:

```bash
python -m pip install -r requirements.txt
python -c "from api.index import app; app.run(port=5000)"
```

## Como publicar no Vercel

```bash
vercel login
vercel            # deploy de teste (preview)
vercel --prod     # deploy de produção
```

Limites a considerar no plano gratuito (Hobby): corpo da requisição de até 4,5 MB por PDF e tempo de execução por função configurado em 30s em `vercel.json` (o plano Hobby aplica um teto próprio; em planos pagos o limite pode ser maior).

## Qualidade e segurança

O núcleo inclui testes automatizados (`test_converter_core.py`, `test_layout.py`, `test_integration.py`) para formatos de data, valores brasileiros, sinais de débito/crédito, detecção de banco, alertas de layout e campos mínimos do OFX. Rode com:

```bash
python -m unittest discover -p "test_*.py"
```

Antes de importar um arquivo em um sistema contábil, confira especialmente o período, o saldo, os sinais e os lançamentos de maior valor.

## Importante sobre PFX e OFX

Os sites de referência tratam de **PDF para OFX**. **PFX/PKCS#12** é outro formato, usado normalmente para certificados digitais e chaves privadas. Se o objetivo real for PFX, o fluxo e os dados de entrada são diferentes; este projeto foi implementado para PDF → OFX.
