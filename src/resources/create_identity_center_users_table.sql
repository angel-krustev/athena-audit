CREATE TABLE IF NOT EXISTS {table} (
  source_identity string,
  identitystore_user string,
  display_name string,
  email string,
  last_update timestamp
)
LOCATION '{location}'
TBLPROPERTIES (
  'table_type' = 'ICEBERG',
  'format' = 'PARQUET'
);
