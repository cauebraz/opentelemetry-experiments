package experimentprocessor

import "go.opentelemetry.io/collector/component"

// Config is empty: the processor always stamps the same attribute.
type Config struct {
	_ struct{}
}

var _ component.Config = (*Config)(nil)

func createDefaultConfig() component.Config {
	return &Config{}
}
