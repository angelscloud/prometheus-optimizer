import re
import os
import subprocess
import time
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
import yaml
import requests
from random import choices
from string import digits
from kubernetes import config
from kubernetes.client import Configuration
from kubernetes.client.api import core_v1_api
from kubernetes.stream import stream
from tempfile import TemporaryFile
import tarfile

with open('config.yaml') as f:
    yaml_config = yaml.safe_load(f)
rules_file_path = 'prometheus-rules.yaml'

def analyze_grafana_metrics():
    print("Analyzing Grafana")
    result = subprocess.run(["mimirtool", "analyze", "grafana", "--address", GRAFANA_ADDRESS, "--key", GRAFANA_API_TOKEN], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    print("STDERR:", result.stderr)
    print("Metrics from Grafana saved")

def k8s_config():
    config.load_incluster_config()
    try:
        c = Configuration().get_default_copy()
    except AttributeError:
        c = Configuration()
        c.assert_hostname = False
    Configuration.set_default(c)
    core_v1 = core_v1_api.CoreV1Api()
    return core_v1

def prometheus_pod_discovery(api_instance, namespace : str, labels : str) -> dict():
    ret = api_instance.list_namespaced_pod(namespace=namespace, label_selector=labels)
    for item in ret.items:
        print(f"Prometheus pod has been successfully discovered. Name: {item.metadata.name}")
        return({"namespace": item.metadata.namespace,
                "pod_name": item.metadata.name,
                "container": item.spec.containers[0].name,
                "pod_ip": item.status.pod_ip
                })

def lookup_prometheus_rules(prom_ip):
    r = requests.get(f"http://{prom_ip}:9090/api/v1/rules")
    r = r.json()
    files = {i.get("file") for i in r.get("data").get("groups")}
    files = list(files)
    return " ".join(files)

def exec_command(api_instance, namespace, pod_name, container, cmd):
    exec_cmd = ['/bin/sh', '-c', cmd]
    resp = stream(api_instance.connect_get_namespaced_pod_exec,
                  pod_name,
                  namespace,
                  container=container,
                  command=exec_cmd,
                  stderr=True, stdin=False,
                  stdout=True, tty=False,
                  _preload_content=False)
    print(f"Preparing to execute a command inside a pod. Pod: {pod_name}, Command: {cmd}")
    while resp.is_open():
        resp.update(timeout=1)
        if resp.peek_stdout():
            return resp.read_stdout()
        if resp.peek_stderr():
            return resp.read_stderr()
    resp.close()
    if resp.returncode != 0:
        raise Exception("Failed to execute command")

def copy_file_from_pod(api_instance, namespace, pod_name, container, source_path, destination_path):
    exec_cmd = ['tar', 'cf', '-', source_path]
    with TemporaryFile() as tar_buffer:
        resp = stream(api_instance.connect_get_namespaced_pod_exec,
                      pod_name,
                      namespace,
                      container=container,
                      command=exec_cmd,
                      stderr=True, stdin=False,
                      stdout=True, tty=False,
                      _preload_content=False)
        while resp.is_open():
            resp.update(timeout=1)
            if resp.peek_stdout():
                out = resp.read_stdout()
                tar_buffer.write(out.encode('utf-8'))
            if resp.peek_stderr():
                pass
        resp.close()

        tar_buffer.flush()
        tar_buffer.seek(0)

        with tarfile.open(fileobj=tar_buffer, mode='r:') as tar:
            for member in tar.getmembers():
                if member.isdir():
                    continue
                fname = member.name.rsplit('/', 1)[1]
                tar.makefile(member, destination_path + '/' + fname)

def unique_name_replacer(text):
    def replacer(match):
        unique_number = ''.join(choices(digits, k=20))
        return f"- name: {unique_number}"
    return re.sub(r"- name:", replacer, text)

def analyze_rules_with_mimirtool():
    global rules_file_path
    subprocess.run(["mimirtool", "analyze", "rule-file", f"/usr/src/app/{rules_file_path}"])

def analyze_prometheus_metrics():
    print("Analyzing Prometheus")
    subprocess.run(["mimirtool", "analyze", "prometheus", "--address", PROMETHEUS_ADDRESS])
    print("Analysis of Prometheus metrics completed. Results saved to prometheus-metrics.json.")

def process_metrics_json():
    os.system('jq -r ".in_use_metric_counts[].metric" prometheus-metrics.json | sort > used_metrics.txt')
    os.system('jq -r ".additional_metric_counts[].metric" prometheus-metrics.json | sort > unused_metrics.txt')
    print("Analyze have been successfully completed")


def mark_as_used(re_pattern) -> None:
    with open("unused_metrics.txt", "r") as f:
        unused_metrics_all = f.readlines()
    re_match = list(filter(re.compile(re_pattern).match, unused_metrics_all))
    unused_metrics = list(filter(lambda x: x not in re_match, unused_metrics_all))
    with open("unused_metrics.txt", "w") as f:
        f.writelines(unused_metrics)

def send_file_to_slack(file_path, channel_id, token):
    client = WebClient(token=token)
    try:
        response = client.files_upload(
            channels=channel_id,
            file=file_path
        )
        assert response["file"]  # the uploaded file
        print(f"File {file_path} uploaded successfully as a snippet.")
    except SlackApiError as e:
        print(f"Error uploading {file_path}: {e}")

def read_metrics(file_path):
    with open(file_path, 'r') as file:
        return [line.strip() for line in file if line.strip()]

def group_metrics_by_prefix(metrics):
    groups = {}
    for metric in metrics:
        prefix = metric.split('_')[0]
        if prefix in groups:
            groups[prefix].append(metric)
        else:
            groups[prefix] = [metric]
    return groups

def generate_relabel_configs(groups):
    relabel_configs = []
    for prefix, metrics in groups.items():
        regex = "|".join([re.escape(metric) for metric in metrics])
        relabel_config = {
            'source_labels': ['__name__'],
            'regex': f'^({regex})$',
            'action': 'drop'
        }
        relabel_configs.append(relabel_config)
    return relabel_configs

def write_config_to_file(config, file_path):
    with open(file_path, 'w') as file:
        yaml.dump(config, file, default_flow_style=False)
    print(f"Config written to {file_path}")


if __name__ == "__main__":
    slack_token = yaml_config['slack']['token']
    slack_channel = yaml_config['slack']['channel']
    if yaml_config['grafana']['enabled']:
        GRAFANA_API_TOKEN = yaml_config['grafana']['api_token']
        GRAFANA_ADDRESS = yaml_config['grafana']['address']
        analyze_grafana_metrics()
    k8s_api = k8s_config()
    if yaml_config['fetch_rules']['enabled']:
        print("Analysing Prometheus rules")
        namespace = yaml_config['fetch_rules']['prometheus_discovery']['namespace']
        labels = yaml_config['fetch_rules']['prometheus_discovery']['labels']
        data = prometheus_pod_discovery(k8s_api, namespace=namespace, labels=labels)
        rules = lookup_prometheus_rules(data.get("pod_ip"))
        if rules:
            exec_command(k8s_api, namespace=data.get("namespace"), pod_name=data.get("pod_name"), container=data.get("container"), cmd=f"for i in {rules} ; do cat $i | yamlfmt - >> /tmp/{rules_file_path}.tmp ; done && sed -e 's/groups://g' -E -e 's/(^#.+)//g' -e '1s/^/groups:/' -e '/^\s*$/d' /tmp/{rules_file_path}.tmp 1> /tmp/{rules_file_path} && rm /tmp/{rules_file_path}.tmp")
            copy_file_from_pod(k8s_api, namespace=data.get("namespace"), pod_name=data.get("pod_name"), container=data.get("container"), source_path=f"/tmp/{rules_file_path}", destination_path="/usr/src/app")
            with open(rules_file_path, "r") as f:
                a = f.read()
            result = unique_name_replacer(a)
            with open(rules_file_path, "w") as f:
                f.write(result)
            analyze_rules_with_mimirtool()
        else:
            print("Skipping, no rules found in pod.")
    if yaml_config['prometheus_analysis']['enabled']:
        PROMETHEUS_ADDRESS = yaml_config['prometheus_analysis']['address']
        analyze_prometheus_metrics()
    process_metrics_json()
    if yaml_config.get('mark_as_used_metric'):
        mark_as_used_metric = yaml_config['mark_as_used_metric']
        mark_as_used(mark_as_used_metric)
    send_file_to_slack('used_metrics.txt', slack_channel, slack_token)
    send_file_to_slack('unused_metrics.txt', slack_channel, slack_token)
    unused_metrics = read_metrics('unused_metrics.txt')
    groups = group_metrics_by_prefix(unused_metrics)
    relabel_configs = generate_relabel_configs(groups)
    prometheus_config = {
        'global': {'scrape_interval': '15s'},
        'remote_write': [{
            'url': 'http://remote-endpoint',
            'write_relabel_configs': relabel_configs
        }]
    }
    write_config_to_file(prometheus_config, 'prometheus_config.yaml')
    send_file_to_slack('prometheus_config.yaml', slack_channel, slack_token)
    time.sleep(1000)