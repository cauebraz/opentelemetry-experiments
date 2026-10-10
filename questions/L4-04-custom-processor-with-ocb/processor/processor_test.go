package experimentprocessor

import (
	"context"
	"testing"

	"go.opentelemetry.io/collector/pdata/plog"
)

func TestProcessLogsStampsEveryRecord(t *testing.T) {
	logs := plog.NewLogs()
	records := logs.ResourceLogs().AppendEmpty().ScopeLogs().AppendEmpty().LogRecords()
	records.AppendEmpty().Body().SetStr("one")
	records.AppendEmpty().Body().SetStr("two")
	records.At(0).Attributes().PutStr("keep", "yes")

	out, err := processLogs(context.Background(), logs)
	if err != nil {
		t.Fatal(err)
	}
	got := out.ResourceLogs().At(0).ScopeLogs().At(0).LogRecords()
	if got.Len() != 2 {
		t.Fatalf("records = %d", got.Len())
	}
	for i := 0; i < got.Len(); i++ {
		value, ok := got.At(i).Attributes().Get(attributeKey)
		if !ok || value.Bool() != true {
			t.Fatalf("record %d missing %s", i, attributeKey)
		}
	}
	kept, ok := got.At(0).Attributes().Get("keep")
	if !ok || kept.Str() != "yes" {
		t.Fatal("existing attribute was not preserved")
	}
}
