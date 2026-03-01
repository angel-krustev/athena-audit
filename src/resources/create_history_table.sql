CREATE EXTERNAL TABLE {table}(
  query_id string,
  query string,
  data_scanned bigint,
  status string)
PARTITIONED BY (
  region string,
  day string,
  workgroup string)
ROW FORMAT SERDE
  'org.openx.data.jsonserde.JsonSerDe'
WITH SERDEPROPERTIES (
  'ignore.malformed.json'='true')
LOCATION
  's3://{bucket}/{prefix}'