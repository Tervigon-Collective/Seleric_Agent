import yaml

with open('/opt/seleric/Seleric_Agent/config/metric_registry.yaml', 'r') as f:
    data = yaml.safe_load(f)

for metric in data.get('metrics', []):
    metric['seleric_module'] = None

with open('/opt/seleric/Seleric_Agent/config/metric_registry.yaml', 'w') as f:
    yaml.dump(data, f, sort_keys=False, default_flow_style=False)
