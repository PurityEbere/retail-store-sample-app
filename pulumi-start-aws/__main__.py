import pulumi
import pulumi_awsx as awsx
import pulumi_eks as eks
import pulumi_kubernetes as k8s
from pathlib import Path

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

namespace_name = "retailstore"

namespace = k8s.core.v1.Namespace(
    namespace_name,
    metadata={"name": namespace_name},
    opts=pulumi.ResourceOptions(provider=k8s_provider)
)

container_repository = awsx.ecr.Repository("retailstore_repository")
pulumi.export("repository_url", container_repository.url)

images = ["app-orders", "app-catalog", "app-cart", "ui-ui", "app-checkout"]

# Build and push all images
built_images = {}
for image in images:
    app_dir = "../src/" + image.split("-")[1]

    built_image = awsx.ecr.Image(
        image,
        repository_url=container_repository.url,
        context=app_dir,
        platform="linux/amd64",
        opts=pulumi.ResourceOptions(
            ignore_changes=["provider"]
        )
    )
    built_images[image] = built_image

# Map service names to their ECR images
service_image_map = {
    "catalog": built_images["app-catalog"].image_uri,
    "cart": built_images["app-cart"].image_uri,
    "orders": built_images["app-orders"].image_uri,
    "ui": built_images["ui-ui"].image_uri,
    "checkout": built_images["app-checkout"].image_uri,
}

# Transformation function to replace images with ECR URIs
def replace_images_with_ecr(obj, opts):
    """
    Transform Kubernetes resources to use ECR images instead of local image names.
    """
    kind = obj.get("kind", "")
    
    # Check if this is a workload resource that has containers
    if kind in ["Deployment", "StatefulSet", "DaemonSet", "Job", "CronJob"]:
        spec = obj.get("spec", {})
        
        # Handle different spec structures
        if kind in ["Deployment", "StatefulSet", "DaemonSet"]:
            template = spec.get("template", {})
            pod_spec = template.get("spec", {})
        elif kind == "Job":
            template = spec.get("template", {})
            pod_spec = template.get("spec", {})
        elif kind == "CronJob":
            job_template = spec.get("jobTemplate", {})
            template = job_template.get("spec", {}).get("template", {})
            pod_spec = template.get("spec", {})
        else:
            pod_spec = {}
        
        # Replace images in containers
        containers = pod_spec.get("containers", [])
        for container in containers:
            current_image = container.get("image", "")
            
            # Check if the image matches any of our service names
            for service_name, ecr_uri in service_image_map.items():
                # Match exact service name or service:tag pattern
                if current_image == service_name or current_image.startswith(f"{service_name}:"):
                    container["image"] = ecr_uri
                    pulumi.log.info(f"Replacing image '{current_image}' with ECR URI for {service_name}")
                    break
        
        # Replace images in init containers if they exist
        init_containers = pod_spec.get("initContainers", [])
        for container in init_containers:
            current_image = container.get("image", "")
            for service_name, ecr_uri in service_image_map.items():
                if current_image == service_name or current_image.startswith(f"{service_name}:"):
                    container["image"] = ecr_uri
                    pulumi.log.info(f"Replacing init container image '{current_image}' with ECR URI for {service_name}")
                    break

# Services to deploy
services = ["cart", "catalog", "checkout", "orders", "ui"]

# Apply Kubernetes manifests for each service using v1 ConfigGroup (which supports transformations)
for service in services:
    kompose_files_dir = f"../src/{service}/kompose_files"
    
    # Check if the directory exists
    if Path(kompose_files_dir).exists():
        # Use v1 ConfigGroup which supports transformations
        k8s.yaml.ConfigGroup(
            f"kompose-{service}",
            files=[f"{kompose_files_dir}/*.yaml"],
            transformations=[replace_images_with_ecr],
            opts=pulumi.ResourceOptions(
                provider=k8s_provider,
                depends_on=[namespace] + list(built_images.values())
            )
        )
        
        pulumi.log.info(f"Applied Kubernetes manifests for {service}")
    else:
        pulumi.log.warn(f"Kompose files directory not found for {service}: {kompose_files_dir}")

pulumi.export("deployed_services", services)