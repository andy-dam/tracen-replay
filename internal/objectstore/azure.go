package objectstore

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net/url"
	"strings"
	"time"

	"github.com/Azure/azure-sdk-for-go/sdk/azcore"
	"github.com/Azure/azure-sdk-for-go/sdk/azcore/to"
	"github.com/Azure/azure-sdk-for-go/sdk/azidentity"
	"github.com/Azure/azure-sdk-for-go/sdk/storage/azblob"
	"github.com/Azure/azure-sdk-for-go/sdk/storage/azblob/blob"
	"github.com/Azure/azure-sdk-for-go/sdk/storage/azblob/bloberror"
	"github.com/Azure/azure-sdk-for-go/sdk/storage/azblob/sas"
	"github.com/Azure/azure-sdk-for-go/sdk/storage/azblob/service"
)

// Azure keeps objects in Blob storage. A key's first segment is the
// container, the rest the blob name. Signed in with the account key (a
// connection string, which is what the Azurite emulator and a development
// account use) it issues service SAS URLs; signed in with a token (managed
// identity, a service principal) it issues user delegation SAS URLs, which
// need the identity to hold Storage Blob Delegator on the account.
type Azure struct {
	client     *azblob.Client
	serviceURL string
	shared     *azblob.SharedKeyCredential
	// created remembers containers this process has ensured exist.
	created map[string]bool
}

// NewAzureFromConnectionString opens the account named by a connection
// string ("DefaultEndpointsProtocol=...;AccountName=...;AccountKey=...").
func NewAzureFromConnectionString(connectionString string) (*Azure, error) {
	client, err := azblob.NewClientFromConnectionString(connectionString, nil)
	if err != nil {
		return nil, err
	}
	name, key := connectionValue(connectionString, "AccountName"), connectionValue(connectionString, "AccountKey")
	shared, err := azblob.NewSharedKeyCredential(name, key)
	if err != nil {
		return nil, err
	}
	return &Azure{client: client, serviceURL: client.URL(), shared: shared, created: map[string]bool{}}, nil
}

// NewAzure opens the account at serviceURL ("https://<account>.blob.core.windows.net")
// with the ambient identity: the environment, a managed identity, or the
// developer's Azure CLI login, whichever answers first.
func NewAzure(serviceURL string) (*Azure, error) {
	credential, err := azidentity.NewDefaultAzureCredential(nil)
	if err != nil {
		return nil, err
	}
	return NewAzureWithCredential(serviceURL, credential)
}

// NewAzureWithCredential opens the account with a token credential.
func NewAzureWithCredential(serviceURL string, credential azcore.TokenCredential) (*Azure, error) {
	client, err := azblob.NewClient(serviceURL, credential, nil)
	if err != nil {
		return nil, err
	}
	return &Azure{client: client, serviceURL: strings.TrimRight(serviceURL, "/"), created: map[string]bool{}}, nil
}

// ServiceURL is the account's blob endpoint ("https://<account>.blob.core.windows.net"):
// the origin a browser talks to when it uploads or plays by a signed URL.
func (a *Azure) ServiceURL() string { return a.serviceURL }

func connectionValue(connectionString, name string) string {
	for _, part := range strings.Split(connectionString, ";") {
		if k, v, ok := strings.Cut(part, "="); ok && strings.EqualFold(strings.TrimSpace(k), name) {
			return strings.TrimSpace(v)
		}
	}
	return ""
}

func split(key string) (containerName, blobName string, err error) {
	if !ValidKey(key) {
		return "", "", fmt.Errorf("objectstore: bad key %q", key)
	}
	containerName, blobName, ok := strings.Cut(key, "/")
	if !ok {
		return "", "", fmt.Errorf("objectstore: key %q names no container", key)
	}
	return containerName, blobName, nil
}

// ensure creates a container the first time this process touches it.
func (a *Azure) ensure(ctx context.Context, containerName string) error {
	if a.created[containerName] {
		return nil
	}
	_, err := a.client.CreateContainer(ctx, containerName, nil)
	if err != nil && !bloberror.HasCode(err, bloberror.ContainerAlreadyExists) {
		return err
	}
	a.created[containerName] = true
	return nil
}

func (a *Azure) Put(ctx context.Context, key string, body io.Reader, size int64, contentType string) error {
	containerName, blobName, err := split(key)
	if err != nil {
		return err
	}
	if err := a.ensure(ctx, containerName); err != nil {
		return err
	}
	options := &azblob.UploadStreamOptions{}
	if contentType != "" {
		options.HTTPHeaders = &blob.HTTPHeaders{BlobContentType: to.Ptr(contentType)}
	}
	_, err = a.client.UploadStream(ctx, containerName, blobName, body, options)
	return err
}

func (a *Azure) Open(ctx context.Context, key string) (io.ReadCloser, error) {
	containerName, blobName, err := split(key)
	if err != nil {
		return nil, err
	}
	response, err := a.client.DownloadStream(ctx, containerName, blobName, nil)
	if err != nil {
		if bloberror.HasCode(err, bloberror.BlobNotFound, bloberror.ContainerNotFound) {
			return nil, ErrNotFound
		}
		return nil, err
	}
	return response.Body, nil
}

func (a *Azure) Stat(ctx context.Context, key string) (Object, error) {
	containerName, blobName, err := split(key)
	if err != nil {
		return Object{}, err
	}
	properties, err := a.client.ServiceClient().NewContainerClient(containerName).NewBlobClient(blobName).GetProperties(ctx, nil)
	if err != nil {
		if bloberror.HasCode(err, bloberror.BlobNotFound, bloberror.ContainerNotFound) {
			return Object{}, ErrNotFound
		}
		return Object{}, err
	}
	object := Object{Key: key}
	if properties.ContentLength != nil {
		object.Size = *properties.ContentLength
	}
	if properties.LastModified != nil {
		object.ModTime = *properties.LastModified
	}
	return object, nil
}

func (a *Azure) Delete(ctx context.Context, key string) error {
	containerName, blobName, err := split(key)
	if err != nil {
		return err
	}
	_, err = a.client.DeleteBlob(ctx, containerName, blobName, nil)
	if err != nil && bloberror.HasCode(err, bloberror.BlobNotFound, bloberror.ContainerNotFound) {
		return nil
	}
	return err
}

func (a *Azure) List(ctx context.Context, prefix string) ([]Object, error) {
	containerName, blobPrefix, _ := strings.Cut(prefix, "/")
	if containerName == "" || !ValidKey(containerName) {
		return nil, fmt.Errorf("objectstore: prefix %q names no container", prefix)
	}
	pager := a.client.NewListBlobsFlatPager(containerName, &azblob.ListBlobsFlatOptions{Prefix: to.Ptr(blobPrefix)})
	var out []Object
	for pager.More() {
		page, err := pager.NextPage(ctx)
		if err != nil {
			if bloberror.HasCode(err, bloberror.ContainerNotFound) {
				return nil, nil
			}
			return nil, err
		}
		for _, item := range page.Segment.BlobItems {
			if item.Name == nil {
				continue
			}
			object := Object{Key: containerName + "/" + *item.Name}
			if item.Properties != nil {
				if item.Properties.ContentLength != nil {
					object.Size = *item.Properties.ContentLength
				}
				if item.Properties.LastModified != nil {
					object.ModTime = *item.Properties.LastModified
				}
			}
			out = append(out, object)
		}
	}
	return out, nil
}

func (a *Azure) PresignUpload(ctx context.Context, key, contentType string, ttl time.Duration) (string, error) {
	return a.presign(ctx, key, ttl, sas.BlobPermissions{Create: true, Write: true}, contentType)
}

func (a *Azure) PresignRead(ctx context.Context, key string, ttl time.Duration) (string, error) {
	return a.presign(ctx, key, ttl, sas.BlobPermissions{Read: true}, "")
}

func (a *Azure) presign(ctx context.Context, key string, ttl time.Duration, permissions sas.BlobPermissions, contentType string) (string, error) {
	containerName, blobName, err := split(key)
	if err != nil {
		return "", err
	}
	if permissions.Create {
		if err := a.ensure(ctx, containerName); err != nil {
			return "", err
		}
	}
	now := time.Now().UTC()
	values := sas.BlobSignatureValues{
		Protocol:      sas.ProtocolHTTPSandHTTP,
		StartTime:     now.Add(-5 * time.Minute),
		ExpiryTime:    now.Add(ttl),
		Permissions:   permissions.String(),
		ContainerName: containerName,
		BlobName:      blobName,
	}
	if contentType != "" {
		values.ContentType = contentType
	}
	var query sas.QueryParameters
	if a.shared != nil {
		query, err = values.SignWithSharedKey(a.shared)
	} else {
		var delegation *service.UserDelegationCredential
		delegation, err = a.client.ServiceClient().GetUserDelegationCredential(ctx, service.KeyInfo{
			Start: to.Ptr(now.Add(-5 * time.Minute).Format(sas.TimeFormat)), Expiry: to.Ptr(now.Add(ttl).Format(sas.TimeFormat))}, nil)
		if err == nil {
			query, err = values.SignWithUserDelegation(delegation)
		}
	}
	if err != nil {
		return "", err
	}
	return a.serviceURL + "/" + url.PathEscape(containerName) + "/" + escapeBlob(blobName) + "?" + query.Encode(), nil
}

// escapeBlob keeps the slashes of a blob name and escapes the rest.
func escapeBlob(name string) string {
	parts := strings.Split(name, "/")
	for i, part := range parts {
		parts[i] = url.PathEscape(part)
	}
	return strings.Join(parts, "/")
}

// IsNotFound reports whether err means a missing object.
func IsNotFound(err error) bool { return errors.Is(err, ErrNotFound) }
