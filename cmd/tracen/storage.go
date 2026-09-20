package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/andy-dam/tracen-replay/internal/objectstore"
	"github.com/andy-dam/tracen-replay/internal/queue"
	"github.com/andy-dam/tracen-replay/internal/store"
)

// Where the files and the queue live. By default everything is on this
// machine's disk and the analyses run inside the service process. With an
// object store the recordings and the analyses' files are objects named by
// key; with a shared queue the service only queues analyses and worker
// processes, started with `tracen worker` anywhere that reaches the same
// database, store and queue, run them.
//
// Credentials never go on the command line: TRACEN_STORAGE_CONNECTION
// holds the storage account's connection string, or TRACEN_STORAGE_ACCOUNT
// names the account and the ambient identity (a managed identity, the
// environment, or the Azure CLI login) signs in; TRACEN_DATABASE_URL is
// the PostgreSQL URL.
type storageFlags struct {
	database    *string
	objectStore *string
	queue       *string
	queueName   *string
}

func addStorageFlags(fs *flag.FlagSet) *storageFlags {
	return &storageFlags{
		database:    fs.String("database", "", "the database: empty for SQLite under -data, or postgres for the PostgreSQL database at TRACEN_DATABASE_URL (what the hosted service and its workers share)"),
		objectStore: fs.String("object-store", "", "where recordings and analysis files are kept: empty for files under -data, dir:PATH for a directory used as an object store, or azure for Blob storage (TRACEN_STORAGE_CONNECTION or TRACEN_STORAGE_ACCOUNT)"),
		queue:       fs.String("shared-queue", "", "a queue shared with worker processes: empty to run analyses in this process, or azure for an Azure Storage Queue that `tracen worker` processes take from (same account as -object-store)"),
		queueName:   fs.String("shared-queue-name", "analyses", "name of the Azure Storage Queue"),
	}
}

// openStore opens the database the flags name.
func (f *storageFlags) openStore(ctx context.Context, dataDir string) (*store.Store, error) {
	switch *f.database {
	case "":
		if err := os.MkdirAll(dataDir, 0o755); err != nil {
			return nil, err
		}
		return store.Open(filepath.Join(dataDir, "tracen.db"))
	case "postgres":
		url := os.Getenv("TRACEN_DATABASE_URL")
		if url == "" {
			return nil, errors.New("-database postgres needs TRACEN_DATABASE_URL in the environment")
		}
		return store.OpenPostgres(ctx, url)
	default:
		return nil, fmt.Errorf("bad -database %q: empty or postgres", *f.database)
	}
}

// open connects to the object store and the queue the flags name. Either
// may be nil when not asked for.
func (f *storageFlags) open(ctx context.Context) (objectstore.Store, queue.Queue, error) {
	var objects objectstore.Store
	switch {
	case *f.objectStore == "":
	case strings.HasPrefix(*f.objectStore, "dir:"):
		local, err := objectstore.NewLocal(strings.TrimPrefix(*f.objectStore, "dir:"))
		if err != nil {
			return nil, nil, fmt.Errorf("-object-store: %w", err)
		}
		objects = local
	case *f.objectStore == "azure":
		connection, account := os.Getenv("TRACEN_STORAGE_CONNECTION"), os.Getenv("TRACEN_STORAGE_ACCOUNT")
		switch {
		case connection != "":
			azure, err := objectstore.NewAzureFromConnectionString(connection)
			if err != nil {
				return nil, nil, fmt.Errorf("-object-store azure: %w", err)
			}
			objects = azure
		case account != "":
			azure, err := objectstore.NewAzure("https://" + account + ".blob.core.windows.net")
			if err != nil {
				return nil, nil, fmt.Errorf("-object-store azure: %w", err)
			}
			objects = azure
		default:
			return nil, nil, errors.New("-object-store azure needs TRACEN_STORAGE_CONNECTION or TRACEN_STORAGE_ACCOUNT in the environment")
		}
	default:
		return nil, nil, fmt.Errorf("bad -object-store %q: empty, dir:PATH or azure", *f.objectStore)
	}
	var q queue.Queue
	switch *f.queue {
	case "":
	case "azure":
		if objects == nil {
			return nil, nil, errors.New("-shared-queue azure needs an -object-store: the workers fetch recordings and store their files there")
		}
		connection, account := os.Getenv("TRACEN_STORAGE_CONNECTION"), os.Getenv("TRACEN_STORAGE_ACCOUNT")
		switch {
		case connection != "":
			azure, err := queue.NewAzureFromConnectionString(ctx, connection, *f.queueName)
			if err != nil {
				return nil, nil, fmt.Errorf("-shared-queue azure: %w", err)
			}
			q = azure
		case account != "":
			azure, err := queue.NewAzure(ctx, "https://"+account+".queue.core.windows.net/"+*f.queueName, nil)
			if err != nil {
				return nil, nil, fmt.Errorf("-shared-queue azure: %w", err)
			}
			q = azure
		default:
			return nil, nil, errors.New("-shared-queue azure needs TRACEN_STORAGE_CONNECTION or TRACEN_STORAGE_ACCOUNT in the environment")
		}
	default:
		return nil, nil, fmt.Errorf("bad -shared-queue %q: empty or azure", *f.queue)
	}
	return objects, q, nil
}
