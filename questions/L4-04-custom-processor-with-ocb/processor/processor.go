package experimentprocessor

import (
	"context"

	"go.opentelemetry.io/collector/pdata/plog"
)

const attributeKey = "experiment.custom_processor"

func processLogs(_ context.Context, ld plog.Logs) (plog.Logs, error) {
	resourceLogs := ld.ResourceLogs()
	for i := 0; i < resourceLogs.Len(); i++ {
		scopeLogs := resourceLogs.At(i).ScopeLogs()
		for j := 0; j < scopeLogs.Len(); j++ {
			records := scopeLogs.At(j).LogRecords()
			for k := 0; k < records.Len(); k++ {
				records.At(k).Attributes().PutBool(attributeKey, true)
			}
		}
	}
	return ld, nil
}
