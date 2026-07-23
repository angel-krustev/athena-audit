CREATE VIEW  s3_access_logs_db.s3_access_events_stage_view AS
SELECT
  CAST(from_iso8601_timestamp(r.eventtime) AS timestamp) event_ts
, r.eventtime
, r.eventname
, r.eventsource
, r.eventtype
, r.eventcategory
, (CASE WHEN (r.eventname LIKE 'Get%') THEN 'READ' WHEN (r.eventname LIKE 'Head%') THEN 'READ' WHEN (r.eventname LIKE 'List%') THEN 'LIST' WHEN (r.eventname LIKE 'Put%') THEN 'WRITE' WHEN (r.eventname LIKE 'Delete%') THEN 'DELETE' ELSE 'OTHER' END) request_type
, r.sourceipaddress
, r.useragent
, r.useridentity.type identity_type
, r.useridentity.arn caller_arn
, r.useridentity.accountid caller_account
, r.useridentity.sessioncontext.sessionissuer.username role_name
, r.useridentity.sessioncontext.sourceidentity source_identity
, COALESCE(NULLIF(r.useridentity.sessioncontext.sourceidentity, ''), element_at(split(r.useridentity.arn, '/'), -1)) effective_identity
, r.useridentity.onbehalfof.userid identitystore_user
, r.requestparameters.bucketname bucket_name
, r.requestparameters.key object_key
, r.additionaleventdata.bytesTransferredIn bytes_in
, r.additionaleventdata.bytesTransferredOut bytes_out
, r.additionaleventdata.AuthenticationMethod auth_method
, r.additionaleventdata.SignatureVersion signature_version
, r.readonly
, r.managementevent
, r.errorcode
, r.errormessage
, (CASE WHEN (r.errorcode IS NULL) THEN 'SUCCESS' ELSE 'FAILED' END) request_status
, r.vpcendpointid
, r.vpcendpointaccountid
, r.tlsdetails.tlsversion tls_version
, r.tlsdetails.ciphersuite cipher_suite
, r.requestid
, r.eventid
, r.sharedeventid
, region
, year
, month
, day
FROM
  (s3_access_logs_db.cloudtrail_raw
CROSS JOIN UNNEST(records) t (r))