# Stackline Crawling

Runtime Python aislado para crear, probar y ejecutar crawlers específicos por sitio.

La IA no normaliza el catálogo ni recibe `crawl-result.json` completo. Trabaja dentro de `build/workspace/`, usa herramientas acotadas para inspeccionar el sitio y modifica el crawler. El crawler generado produce directamente el JSON contractual; el runtime calcula health y devuelve a la IA únicamente métricas, errores y razones de calidad.

## Flujo

```text
IA crea/modifica build/workspace/
  -> run_workspace
  -> workspace escribe build/results/crawl-result.json
  -> runtime valida contrato y calcula health
  -> IA recibe solo health, métricas y errores
  -> si hace falta, usa jq/awk/grep sobre artefactos con salida limitada
```

El paquete no conoce la base de datos de Stackline ni valida semántica de productos/categorías. Esa responsabilidad pertenece al backend/UI.

## Uso local

```bash
python3.12 -m venv .venv
. .venv/bin/activate

## Cuándo se invoca la IA

La IA no se ejecuta en cada corrida:

1. Se invoca para crear el primer crawler cuando no existe `workspace/manifest.json`.
2. Se invoca para corregirlo cuando una ejecución técnica falla o el health queda debajo del threshold.
3. Se invoca cuando llega feedback específico del usuario, aunque el health actual sea saludable.
4. Si el crawler existente pasa health y no hay feedback, el runtime devuelve el resultado sin llamar al modelo.

python -m pip install -e '.[audit]'

export OPENAI_API_KEY='...'
export AI_CHAT_MODEL='gpt-6-luna'
export AI_MODE='real'

stackline-crawl run \
  --site-key san-miguel \
  --site-url https://www.sanmiguelmadera.com/ \
  --sop ../stack-line-services/src/main/resources/ai/sops/san-miguel/catalog-v1.md \
  --build-root ../.local/crawling/clients/demo/suppliers/san-miguel/sites/site-1/build
```

Para validar solo el crawler determinista heredado durante desarrollo:

```bash
AI_MODE=fake stackline-crawl run \
  --site-key san-miguel \

## Boilerplate inicial

Si el sitio aún no tiene crawler, el runtime crea automáticamente el scaffold antes de invocar la IA. Incluye `manifest.json`, `crawl.py`, `config.json`, `site-sop.md` y helpers en `lib/`. `crawl.py` falla explícitamente con `CRAWLER_NOT_IMPLEMENTED` hasta que el agente implemente la lógica específica del sitio. Después de producir un resultado contractual evaluable, el manifest pasa de `SCAFFOLD` a `READY`.
  --site-url https://www.sanmiguelmadera.com/shop \
  --build-root ../.local/crawling/clients/demo/suppliers/san-miguel/sites/site-1/build
```

## Workspace del sitio

`build/workspace/` se conserva entre corridas para que el agente pueda mejorar el crawler:

```text
build/
├── workspace/
│   ├── site-sop.md
│   ├── manifest.json
│   ├── crawl.py

El agente debe revisar el índice al terminar cada investigación. Si descubrió una nuance durable que no estaba registrada, debe leer la sección correspondiente y actualizarla con `sop_update`; si no hay conocimiento nuevo, no debe modificar el archivo.
│   └── helpers.py
├── run-state.json
├── logs/
│   ├── agent-transcript.jsonl
│   ├── runner.stdout.log
│   └── runner.stderr.log
├── results/
│   ├── crawl-result.json
│   └── health-result.json
└── evidence/
    ├── agent/
    └── html/
```

El `manifest.json` debe declarar un `entrypoint` Python. El entrypoint recibe:

```text
--site-url URL
--output /ruta/al/build/results/crawl-result.json
--run-id UUID
```

## Consultas de artefactos

La IA puede consultar datos bajo demanda usando `jq`, `awk`, `grep`, `head`, `tail`, `sort`, `uniq`, `wc` y scripts Python temporales escritos dentro del workspace. Las consultas se ejecutan con cwd en `build/`, timeout y límite de salida; no existe un DSL propio.

## Resultados

El runtime mantiene únicamente la última corrida en el `build/` recibido y devuelve:

- `COMPLETED`
- `EXECUTION_FAILED`
- `QUALITY_FAILED`

Los intentos de health se limitan a tres. Los errores técnicos del crawler no consumen esos intentos; tienen un límite separado de iteraciones técnicas.
