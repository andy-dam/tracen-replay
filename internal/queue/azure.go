package queue

import (
	"context"
	"errors"
	"time"

	"github.com/Azure/azure-sdk-for-go/sdk/azcore"
	"github.com/Azure/azure-sdk-for-go/sdk/azcore/to"
	"github.com/Azure/azure-sdk-for-go/sdk/azidentity"
	"github.com/Azure/azure-sdk-for-go/sdk/storage/azqueue"
)

// Azure is an Azure Storage Queue. Messages live up to seven days; a
// message taken and never deleted comes back when its hold lapses, and
// the storage counts how many times that happened.
type Azure struct {
	client *azqueue.QueueClient
}

// NewAzureFromConnectionString opens (creating if needed) the named queue
// of the account in the connection string.
func NewAzureFromConnectionString(ctx context.Context, connectionString, queueName string) (*Azure, error) {
	client, err := azqueue.NewQueueClientFromConnectionString(connectionString, queueName, nil)
	if err != nil {
		return nil, err
	}
	return ready(ctx, client)
}

// NewAzure opens (creating if needed) the queue at queueURL
// ("https://<account>.queue.core.windows.net/<queue>") with the ambient
// identity, or with credential when given.
func NewAzure(ctx context.Context, queueURL string, credential azcore.TokenCredential) (*Azure, error) {
	if credential == nil {
		var err error
		if credential, err = azidentity.NewDefaultAzureCredential(nil); err != nil {
			return nil, err
		}
	}
	client, err := azqueue.NewQueueClient(queueURL, credential, nil)
	if err != nil {
		return nil, err
	}
	return ready(ctx, client)
}

func ready(ctx context.Context, client *azqueue.QueueClient) (*Azure, error) {
	if _, err := client.Create(ctx, nil); err != nil {
		var responseError *azcore.ResponseError
		if !errors.As(err, &responseError) || responseError.StatusCode != 204 && responseError.StatusCode != 409 {
			return nil, err
		}
	}
	return &Azure{client: client}, nil
}

func seconds(d time.Duration) *int32 {
	return to.Ptr(int32(d / time.Second))
}

func (q *Azure) Enqueue(ctx context.Context, body string) error {
	_, err := q.client.EnqueueMessage(ctx, body, nil)
	return err
}

func (q *Azure) Receive(ctx context.Context, visibility time.Duration) (Message, bool, error) {
	response, err := q.client.DequeueMessage(ctx, &azqueue.DequeueMessageOptions{VisibilityTimeout: seconds(visibility)})
	if err != nil {
		return Message{}, false, err
	}
	if len(response.Messages) == 0 {
		return Message{}, false, nil
	}
	m := response.Messages[0]
	message := Message{}
	if m.MessageID != nil {
		message.ID = *m.MessageID
	}
	if m.PopReceipt != nil {
		message.Receipt = *m.PopReceipt
	}
	if m.MessageText != nil {
		message.Body = *m.MessageText
	}
	if m.DequeueCount != nil {
		message.Dequeued = int(*m.DequeueCount)
	}
	return message, true, nil
}

func (q *Azure) Extend(ctx context.Context, m Message, visibility time.Duration) (Message, error) {
	response, err := q.client.UpdateMessage(ctx, m.ID, m.Receipt, m.Body, &azqueue.UpdateMessageOptions{VisibilityTimeout: seconds(visibility)})
	if err != nil {
		var responseError *azcore.ResponseError
		if errors.As(err, &responseError) && (responseError.StatusCode == 404 || responseError.StatusCode == 400) {
			return Message{}, ErrStale
		}
		return Message{}, err
	}
	if response.PopReceipt != nil {
		m.Receipt = *response.PopReceipt
	}
	return m, nil
}

func (q *Azure) Delete(ctx context.Context, m Message) error {
	_, err := q.client.DeleteMessage(ctx, m.ID, m.Receipt, nil)
	if err != nil {
		var responseError *azcore.ResponseError
		if errors.As(err, &responseError) && responseError.StatusCode == 404 {
			return nil
		}
		return err
	}
	return nil
}
