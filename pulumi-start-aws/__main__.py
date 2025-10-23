import pulumi
import pulumi_awsx as awsx
import pulumi_eks as eks
import pulumi_kubernetes as k8s

config = pulumi.Config()
min_cluster_size = config.get_int("minClusterSize", 1)
max_cluster_size = config.get_int("maxClusterSize", 2)
desired_cluster_size = config.get_int("desiredClusterSize", 2)
eks_node_instance_type = config.get("eksNodeInstanceType", "t3.medium")
vpc_network_cidr = config.get("vpcNetworkCidr", "10.0.0.0/16")

eks_vpc = awsx.ec2.Vpc("eks-vpc",
    enable_dns_hostnames=True,
    cidr_block=vpc_network_cidr)

eks_cluster = eks.Cluster("eks-cluster",
    vpc_id=eks_vpc.vpc_id,
    authentication_mode=eks.AuthenticationMode.API,
    public_subnet_ids=eks_vpc.public_subnet_ids,
    private_subnet_ids=eks_vpc.private_subnet_ids,
    instance_type=eks_node_instance_type,
    desired_capacity=desired_cluster_size,
    min_size=min_cluster_size,
    max_size=max_cluster_size,
    node_associate_public_ip_address=False,
    endpoint_private_access=False,
    endpoint_public_access=True
)

# Export values to use elsewhere
pulumi.export("kubeconfig", eks_cluster.kubeconfig)
pulumi.export("vpcId", eks_vpc.vpc_id)


k8s_provider = k8s.Provider(
    "k8s-provider",
    kubeconfig=eks_cluster.kubeconfig
)

namespace = k8s.core.v1.Namespace(
    "app-namespace",
    metadata={ "name": "app-namespace" },
    opts=pulumi.ResourceOptions(provider=k8s_provider)
)

timeapi_labels = { "app": "timeapi" }
nginx_labels = { "app": "nginx" }

# Workload A: timeapi
timeapi_deployment = k8s.apps.v1.Deployment(
    "timeapi-deployment",
    metadata={ "namespace": namespace.metadata["name"], "labels": timeapi_labels },
    spec={
        "selector": { "matchLabels": timeapi_labels },
        "replicas": 2,
        "template": {
            "metadata": { "labels": timeapi_labels },
            "spec": {
                "containers": [
                    {
                        "name": "timeapi",
                        "image": "vicradon/timeapi:latest",
                        "ports": [ { "containerPort": 4500 } ],
                        # add env/volumes if needed
                    }
                ]
            }
        }
    },
    opts=pulumi.ResourceOptions(provider=k8s_provider)
)

timeapi_service = k8s.core.v1.Service(
    "timeapi-service",
    metadata={ "namespace": namespace.metadata["name"], "labels": timeapi_labels },
    spec={
        "type": "LoadBalancer",
        "selector": timeapi_labels,
        "ports": [ { "port": 4500, "targetPort": 4500 } ]
    },
    opts=pulumi.ResourceOptions(provider=k8s_provider)
)

# Workload B: nginx
nginx_deployment = k8s.apps.v1.Deployment(
    "nginx-deployment",
    metadata={ "namespace": namespace.metadata["name"], "labels": nginx_labels },
    spec={
        "selector": { "matchLabels": nginx_labels },
        "replicas": 2,
        "template": {
            "metadata": { "labels": nginx_labels },
            "spec": {
                "containers": [
                    {
                        "name": "nginx",
                        "image": "nginx:latest",
                        "ports": [ { "containerPort": 80 } ],
                    }
                ]
            }
        }
    },
    opts=pulumi.ResourceOptions(provider=k8s_provider)
)

nginx_service = k8s.core.v1.Service(
    "nginx-service",
    metadata={ "namespace": namespace.metadata["name"], "labels": nginx_labels },
    spec={
        "type": "LoadBalancer",
        "selector": nginx_labels,
        "ports": [ { "port": 80, "targetPort": 80 } ]
    },
    opts=pulumi.ResourceOptions(provider=k8s_provider)
)

pulumi.export("timeapi_endpoint", 
    timeapi_service.status.apply(
        lambda s: s.load_balancer.ingress[0].hostname or s.load_balancer.ingress[0].ip 
        if s.load_balancer and s.load_balancer.ingress else None
    )
)

pulumi.export("nginx_endpoint", 
    nginx_service.status.apply(
        lambda s: s.load_balancer.ingress[0].hostname or s.load_balancer.ingress[0].ip 
        if s.load_balancer and s.load_balancer.ingress else None
    )
)