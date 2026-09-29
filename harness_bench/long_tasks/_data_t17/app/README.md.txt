# stockroom — складской учёт

Небольшое приложение складского учёта оптово-розничной компании: справочник товаров,
приходы, заказы покупателей и отгрузки, возвраты, инвентаризация, себестоимость по
FIFO, счета, отчёты, выгрузки. Данные — в одном JSON-файле.

    python3 -m stockroom --db stock.json init --company "ООО «Ромашка»"
    python3 -m stockroom --db stock.json item add MILK "Молоко 1 л" --price 89.90 --vat 10
    python3 -m stockroom --db stock.json report stock

* Документация: `docs/USAGE.md` (команды), `docs/ARCHITECTURE.md` (устройство кода и
  данных), `docs/CHANGELOG.md` (история версий).
* Тесты: `python3 -m unittest discover -s tests` (только стандартная библиотека,
  Python 3.9+).
* Текущая доработка: `FEATURE.md`.
