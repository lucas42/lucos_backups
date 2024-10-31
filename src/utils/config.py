import yaml

with open("config.yaml") as config_yaml:
	config = yaml.safe_load(config_yaml)

def getAllDomains(ignore_host):
	domainlist = []
	for hostname in config["hosts"]:
		target_domain = config["hosts"][hostname]["domain"]
		if target_domain != ignore_host.domain:
			domainlist.append(target_domain)
	return domainlist

