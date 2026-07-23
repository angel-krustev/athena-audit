CREATE OR REPLACE VIEW s3_access_logs_db.s3_access_events AS
SELECT
    CAST(from_iso8601_timestamp(r.eventtime) AS timestamp) AS event_ts,

    r.eventtime,
    r.eventname,
    r.eventsource,
    r.eventtype,
    r.eventcategory,

    CASE
        WHEN r.eventname LIKE 'Get%'    THEN 'READ'
        WHEN r.eventname LIKE 'Head%'   THEN 'READ'
        WHEN r.eventname LIKE 'List%'   THEN 'LIST'
        WHEN r.eventname LIKE 'Put%'    THEN 'WRITE'
        WHEN r.eventname LIKE 'Delete%' THEN 'DELETE'
        ELSE 'OTHER'
    END AS request_type,

    r.sourceipaddress,
    r.useragent,

    r.useridentity.type      AS identity_type,
    r.useridentity.arn       AS caller_arn,
    r.useridentity.accountid AS caller_account,

    r.useridentity.sessioncontext.sessionissuer.username AS role_name,
    COALESCE(
        NULLIF(r.useridentity.sessioncontext.sourceidentity, ''),
        icu.source_identity
    ) AS source_identity,

    -- For SAML/SSO users source_identity is often empty; prefer the
    -- Identity Center lookup value, then fall back to caller ARN session name.
    COALESCE(
        NULLIF(r.useridentity.sessioncontext.sourceidentity, ''),
        icu.source_identity,
        element_at(split(r.useridentity.arn, '/'), -1)
    ) AS effective_identity,

    r.useridentity.onbehalfof.userid AS identitystore_user,

    r.requestparameters.bucketname AS bucket_name,
    r.requestparameters.key        AS object_key,

    r.additionaleventdata.bytesTransferredIn  AS bytes_in,
    r.additionaleventdata.bytesTransferredOut AS bytes_out,

    r.additionaleventdata.AuthenticationMethod AS auth_method,
    r.additionaleventdata.SignatureVersion     AS signature_version,

    r.readonly,
    r.managementevent,

    r.errorcode,
    r.errormessage,

    CASE
        WHEN r.errorcode IS NULL THEN 'SUCCESS'
        ELSE 'FAILED'
    END AS request_status,

    r.vpcendpointid,
    r.vpcendpointaccountid,

    r.tlsdetails.tlsversion   AS tls_version,
    r.tlsdetails.ciphersuite  AS cipher_suite,

    r.requestid,
    r.eventid,
    r.sharedeventid,

    region,
    year,
    month,
    day

FROM s3_access_logs_db.cloudtrail_raw
CROSS JOIN UNNEST(records) AS t(r)
LEFT JOIN s3_access_logs_db.identity_center_users AS icu
    ON r.useridentity.onbehalfof.userid = icu.identitystore_user;
