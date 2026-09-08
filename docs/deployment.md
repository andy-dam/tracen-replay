# Deployment design

Azure is the target platform. Infrastructure templates and deployment commands will be added with the implementation; this document describes the intended topology and operational requirements.

## Resources

| Resource | Responsibility |
|---|---|
| Container Apps, Consumption | Go API and compiled frontend, HTTPS ingress, application revisions |
| Container Apps Jobs | Finite CPU video-processing tasks and scheduled recovery |
| Blob Storage | Private source recordings, thumbnails, report bundles, model artifacts |
| SQL Database | Runs, job leases, corrections, reservations, transactional outbox |
| Queue Storage | Job notifications and poison-message handling |
| Entra ID / managed identities | User authentication and service-to-service permissions |
| Container Registry | Versioned application and worker images |
| Azure Monitor / Log Analytics | Operational metrics and bounded diagnostic logs |
| Bicep | Reproducible resource configuration |

The frontend and API will share one origin. Application and worker images will be separate so inference dependencies do not increase the web service's footprint.

## Release sequence

1. Deploy a sample-report application and verify HTTPS, health checks, logs, and rollback.
2. Configure user authentication and separate application/worker identities.
3. Add private storage, SQL migrations, upload admission, and ownership checks.
4. Run the worker manually on a fixture, then enable queue-triggered execution.
5. Add bounded scheduled outbox recovery and retention cleanup.
6. Deploy a checksum-pinned model after local evaluation and CPU profiling.

The queue scaler starts job executions; worker code remains responsible for receiving messages, extending visibility, and acknowledging terminal outcomes. [Container Apps Jobs](https://learn.microsoft.com/en-us/azure/container-apps/jobs)

Managed identities will provide resource access without embedding storage-account keys in application code. User authentication remains a separate concern. [Managed identities](https://learn.microsoft.com/en-us/azure/container-apps/managed-identity), [application authentication](https://learn.microsoft.com/en-us/azure/container-apps/authentication)

## Configuration

Deployment configuration will specify the resource group and region, image digests, model checksum, authentication settings, storage locations, SQL connection settings, queue names, and application limits.

Credentials will be provided through managed identity or deployment secret configuration. Browser assets will contain no database or storage credentials. SQL access will use an explicitly authorized principal and the minimum required network access.

Initial public access will be limited to prepared sample reports. Video submission will be enabled only after authentication, ownership isolation, admission limits, and cleanup have been verified.

## Resource limits

The following are initial application settings, subject to profiling:

| Setting | Initial value |
|---|---|
| Accepted media | H.264 MP4, up to 1080p / 60 fps input |
| Clip duration / size | 120 seconds / 40 MiB |
| Raw-media reservation ceiling | 400 MiB, including pending uploads |
| Report bundle | Up to 10 MiB |
| Admission | One active job per user; five jobs/day globally |
| Worker concurrency | One execution |
| Per-attempt timeout / attempts | Ten minutes / three |
| Raw-media retention | 24 hours |

Frame sampling will keep decoded working sets bounded. Model size, CPU latency, and peak memory will determine worker allocation. Longer clips require revised runtime and capacity measurements.

The API will initially scale from zero to one replica. SQL connection lifetimes and recovery schedules will account for idle behavior. Retention will combine Blob lifecycle rules with application cleanup; lifecycle deletion may occur after the configured expiration time.

## Capacity and cost

Compute, database activity, storage capacity and operations, queue requests, registry storage, log ingestion, and network transfer are separate consumption categories. Free allowances, where applicable, do not establish a zero-cost guarantee for the full deployment. Current pricing and subscription limits should be checked before provisioning. [Container Apps billing](https://learn.microsoft.com/en-us/azure/container-apps/billing), [SQL free-offer behavior](https://learn.microsoft.com/en-us/azure/azure-sql/database/free-offer?view=azuresql)

Admission controls will reserve capacity before uploads and reject new work when limits are reached. A global pause will disable new analyses without interrupting report export. Billing alerts will supplement resource limits, not replace them.

## Release verification and rollback

Each release will record source revision, image digests, schema migration version, model checksum, and infrastructure revision. Verification will cover a fixture run, authorization failures, retry recovery, and report compatibility.

Application and model rollback will restore pinned prior artifacts. Database changes will favor backward-compatible migrations; destructive changes require an export and a recovery plan.

Operational reports will record input bytes/duration, execution time, allocated CPU/memory, retained artifacts, and measured service consumption. Logs will omit tokens, signed URLs, and full recordings.

## Teardown

Before removing an environment, admission will be disabled, active jobs drained or cancelled, and retained reports exported. Cleanup will inventory storage, registry, database, queues, and diagnostics resources in addition to the application itself. Local fixture processing and exported reports will remain independent of the hosted environment.
