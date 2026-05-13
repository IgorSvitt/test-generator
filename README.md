# llm-mutation-test-generator

Инструмент для автоматической генерации `pytest`-тестов с помощью LLM и обратной
связью от mutation testing.

Сервис генерирует тесты для одной Python-функции, запускает их через `pytest`,
проверяет качество через `mutmut`, а затем передает выжившие мутанты обратно в
следующую итерацию генерации. Это позволяет оценивать не только покрытие строк,
но и способность тестов находить реальные изменения в логике.

## Возможности

- статический анализ функции через `ast`;
- генерация pytest-тестов через LiteLLM;
- запуск тестов в отдельном subprocess;
- mutation testing через `mutmut`;
- итеративное улучшение тестов по списку выживших мутантов;
- CLI-запуск через `python -m test_generator` или `test-generator`.

## Установка для локальной разработки

```bash
cd test_generator
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Проверить установку:

```bash
python -m test_generator --help
mutmut --version
pytest --version
```

## Настройка LLM

Создайте файл `.env` в папке `test_generator`:

```env
LLM_PROVIDER=openai
LLM_MODEL=gpt-4o-mini
LLM_API_KEY=your_key
LLM_API_BASE=
```

Примеры провайдеров:

```env
# OpenAI
LLM_PROVIDER=openai
LLM_MODEL=gpt-4o-mini
LLM_API_KEY=sk-...
```

```env
# Gemini через API key
LLM_PROVIDER=gemini
LLM_MODEL=gemini-1.5-flash
LLM_API_KEY=AIza...
```

```env
# Vertex AI, использует ADC credentials
LLM_PROVIDER=vertex
LLM_MODEL=gemini-1.5-pro
LLM_API_KEY=
```

Никогда не коммитьте `.env`: API-ключи должны оставаться локальными.

## Запуск

Из папки `test_generator` с активированным `.venv`:

```bash
python -m test_generator \
  --project-root ../ecommerce_backend_eval \
  --file src/payment_service.py \
  --function process_payment
```

Параметры:

- `--project-root` — корень проекта, для которого генерируются тесты;
- `--file` — путь к исходному файлу относительно `project-root`;
- `--function` — имя функции, для которой нужно сгенерировать тесты;
- `--mutation-threshold` — целевой mutation score, по умолчанию `80`;
- `--max-iterations` — максимум итераций генерации, по умолчанию `3`.

Пример с явным порогом:

```bash
python -m test_generator \
  --project-root ../ecommerce_backend_eval \
  --file src/discounts.py \
  --function calculate_bulk_discount \
  --mutation-threshold 100 \
  --max-iterations 3
```

Сгенерированные тесты сохраняются в:

```text
<project-root>/tests/test_<module>.py
```

Например:

```text
ecommerce_backend_eval/tests/test_payment_service.py
```

## Что записывать после эксперимента

После каждого запуска CLI выводит итоговый блок `Results`. Для сравнения систем
удобно фиксировать:

```text
Function:
Tests generated:
Iterations:
Mutation score:
Survived mutants:
Time elapsed:
```

Если тесты не прошли, mutation testing не запускается. В таком случае mutation
score нужно записывать как `0%` или `n/a`, а в комментарии указать, что генерация
завершилась падающими тестами.

## Как работает pipeline

1. `analyzer.py` извлекает код функции, аргументы, тип возврата, импорты, вызовы
   и возможные side effects.
2. `generator.py` строит prompt и получает Python-код тестов от LLM.
3. `runner.py` записывает тестовый файл и запускает `pytest`.
4. `mutator.py` запускает `mutmut`, фильтрует результаты по целевой функции и
   формирует описание выживших мутантов.
5. `pipeline.py` повторяет цикл, пока mutation score не достигнет порога или не
   закончится лимит итераций.

## Ручная проверка тестов

Если тесты были написаны вручную или сгенерированы сторонним инструментом:

```bash
cd ../ecommerce_backend_eval
source ../test_generator/.venv/bin/activate
PYTHONPATH=src pytest tests/test_payment_service.py -q
```

Для ручного запуска `mutmut` создайте `setup.cfg` в тестируемом проекте:

```ini
[mutmut]
paths_to_mutate = src/payment_service.py
also_copy = src
pytest_add_cli_args =
    tests/test_payment_service.py
    -q
```

Затем:

```bash
mutmut run
mutmut results --all true
```

## Публикация пакета

Сборка:

```bash
python3 -m pip install --upgrade build twine
python3 -m build
python3 -m twine check dist/*
```

Загрузка в TestPyPI:

```bash
python3 -m twine upload --repository testpypi dist/*
```

Загрузка в PyPI:

```bash
python3 -m twine upload dist/*
```

Для PyPI используйте API token: username `__token__`, password — значение токена.
