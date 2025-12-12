import yaml, requests
from utils.schedule_tracker import updateScheduleTracker

inactive_host_list = ['virgon-express'] # Not currently online.  TODO: handle offline hosts more gracefully
config = {}

def getVolumesConfig():
	return config["volumes"]

def getHostsConfig():
	active_hosts = {}
	for host in config["hosts"]:
		if host not in inactive_host_list:
			active_hosts[host] = config["hosts"][host]
	return active_hosts

def getAllDomains(ignore_host):
	domainlist = []
	for hostname in config["hosts"]:
		target_domain = config["hosts"][hostname]["domain"]
		if hostname not in inactive_host_list and target_domain != ignore_host.domain:
			domainlist.append(target_domain)
	return domainlist

def fetchConfig():
	print ("\033[0mFetching config...", flush=True)
	try:

		volume_resp = requests.get("https://configy.l42.eu/volumes", headers={
			"Accept": "application/x-yaml",
			"User-Agent": "lucos_backups",
		})
		volume_resp.raise_for_status()
		volume_yaml = volume_resp.content.decode("utf-8")
		config["volumes"] = {}
		for volume in yaml.safe_load(volume_yaml):
			config["volumes"][volume['id']] = volume

		host_resp = requests.get("https://configy.l42.eu/hosts", headers={
			"Accept": "application/x-yaml",
			"User-Agent": "lucos_backups",
		})
		host_resp.raise_for_status()
		host_yaml = host_resp.content.decode("utf-8")
		config["hosts"] = {}
		for host in yaml.safe_load(host_yaml):
			config["hosts"][host['id']] = host

		yaml.dump(config, config_file, default_flow_style=False)
		print("\033[92m" + "Config fetched successfully" + "\033[0m", flush=True)
		updateScheduleTracker(
			system="lucos_backups_config",
			success=True,
			frequency=60*60, # 1 hour in seconds
		)
	except Exception as error:
		print ("\033[91m** Error ** " + str(error) + "\033[0m", flush=True)
		updateScheduleTracker(
			system="lucos_backups_config",
			success=False,
			message=str(error),
			frequency=60*60, # 1 hour in seconds
		)
		raise error

try:
	config_file = open('config.yaml', 'r+')
	read_config = yaml.safe_load(config_file)
	if read_config:
		config = read_config
	else:
		fetchConfig()
except FileNotFoundError:
	config_file = open('config.yaml', 'w+')
	fetchConfig()
