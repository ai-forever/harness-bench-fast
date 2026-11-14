# result-reporting

## ADDED Requirements

### Requirement: Row place column in fork tables

The fork page `docs/fork.html` SHALL render a leading place column (`#`) as the first
column of every configuration table (Shot List, Light, Medium, Medium sample). Each data
row SHALL carry a row number equal to its sequential position in the current display
order, and the numbering SHALL be continuous across the `Our runs` and `Upstream
comparison` groups. Group header rows SHALL NOT carry a place number.

#### Scenario: Place number on each data row

- **WHEN** a fork table is rendered
- **THEN** every data row SHALL have a place number in its first column, and group header
  rows SHALL NOT have a number

#### Scenario: Numbering continues across groups

- **WHEN** a table has an `Our runs` group followed by an `Upstream comparison` group
- **THEN** the first upstream row's number SHALL continue the sequence after the last
  `Our runs` row, rather than restarting at 1

#### Scenario: Number follows the current display order

- **WHEN** rows are reordered by a column sort
- **THEN** the place numbers SHALL be recomputed so that `#` reflects the new display order

### Requirement: Column sorting on fork tables

Configuration tables on `docs/fork.html` SHALL be sortable by clicking a column header.
Sorting SHALL apply within each group separately, group header rows SHALL stay in place,
and rows with a missing value (`—` or empty) SHALL be placed after the populated rows
regardless of sort direction.

#### Scenario: Sortable metric and text columns

- **WHEN** a fork table is rendered
- **THEN** the numeric metrics `%`, `Coverage`, `Time`, `Steps`, `Tokens (cost)` and
  `Result`, as well as the text columns (`Harness`, `Profile`, `Model`, `Subset`,
  `Version`, `Status`), SHALL be clickable and sortable

#### Scenario: First click sorts ascending

- **WHEN** the user clicks a sortable column header
- **THEN** the data rows SHALL be sorted by that column in ascending order

#### Scenario: Second click sorts descending

- **WHEN** the user clicks the same column header again
- **THEN** the data rows SHALL be sorted by that column in descending order

#### Scenario: Sorting respects groups

- **WHEN** a sortable column is sorted
- **THEN** the rows SHALL be ordered within each group, and the group header rows SHALL
  remain at the head of their own group

#### Scenario: Missing values sort last

- **WHEN** a column is sorted and some rows show `—`
- **THEN** the rows with `—` SHALL be placed after rows that have a value, in both the
  ascending and the descending direction

#### Scenario: Numeric and ratio metrics parse correctly

- **WHEN** a numeric or ratio column (`%`, `Coverage`, `Time`, `Steps`, `Tokens (cost)`,
  `Result`) is sorted
- **THEN** rows SHALL be ordered by the underlying numeric value rather than by the
  displayed text (thousands separators and the `X/Y` ratio SHALL be parsed, not compared
  as strings)
