CREATE EXTERNAL TABLE IF NOT EXISTS s3_access_logs_db.cloudtrail_raw (
    Records ARRAY<
        STRUCT<
            eventVersion         : STRING,

            userIdentity : STRUCT<
                type             : STRING,
                principalId      : STRING,
                arn              : STRING,
                accountId        : STRING,
                accessKeyId      : STRING,

                sessionContext : STRUCT<
                    sessionIssuer : STRUCT<
                        type        : STRING,
                        principalId : STRING,
                        arn         : STRING,
                        accountId   : STRING,
                        userName    : STRING
                    >,
                    attributes : STRUCT<
                        creationDate     : STRING,
                        mfaAuthenticated : STRING
                    >,
                    sourceIdentity : STRING
                >,

                onBehalfOf : STRUCT<
                    userId           : STRING,
                    identityStoreArn : STRING
                >
            >,

            eventTime          : STRING,
            eventSource        : STRING,
            eventName          : STRING,
            awsRegion          : STRING,
            sourceIPAddress    : STRING,
            userAgent          : STRING,

            requestParameters : STRUCT<
                bucketName : STRING,
                Host       : STRING,
                key        : STRING
            >,

            responseElements : STRING,

            additionalEventData : STRUCT<
                SignatureVersion      : STRING,
                CipherSuite           : STRING,
                bytesTransferredIn    : STRING,
                AuthenticationMethod  : STRING,
                `x-amz-id-2`          : STRING,
                bytesTransferredOut   : BIGINT
            >,

            requestID          : STRING,
            eventID            : STRING,
            readOnly           : BOOLEAN,

            resources : ARRAY<
                STRUCT<
                    accountId : STRING,
                    type      : STRING,
                    ARN       : STRING,
                    ARNPrefix : STRING
                >
            >,

            eventType          : STRING,
            managementEvent    : BOOLEAN,
            recipientAccountId : STRING,
            sharedEventID      : STRING,

            vpcEndpointId      : STRING,
            vpcEndpointAccountId : STRING,

            eventCategory      : STRING,

            tlsDetails : STRUCT<
                tlsVersion              : STRING,
                cipherSuite             : STRING,
                clientProvidedHostHeader : STRING
            >,

            errorCode    : STRING,
            errorMessage : STRING
        >
    >
)
PARTITIONED BY (
    region STRING,
    year   STRING,
    month  STRING,
    day    STRING
)
ROW FORMAT SERDE 'org.openx.data.jsonserde.JsonSerDe'
LOCATION 's3://aws-logs-991536801739-us-east-1/s3-access-trail/AWSLogs/991536801739/CloudTrail/'
TBLPROPERTIES (
    'projection.enabled'        = 'true',

    'projection.region.type'    = 'enum',
    'projection.region.values'  = 'us-east-1',

    'projection.year.type'      = 'integer',
    'projection.year.range'     = '2024,2035',

    'projection.month.type'     = 'integer',
    'projection.month.range'    = '1,12',
    'projection.month.digits'   = '2',

    'projection.day.type'       = 'integer',
    'projection.day.range'      = '1,31',
    'projection.day.digits'     = '2',

    'storage.location.template' = 's3://aws-logs-991536801739-us-east-1/s3-access-trail/AWSLogs/991536801739/CloudTrail/${region}/${year}/${month}/${day}/'
);
