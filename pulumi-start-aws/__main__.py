import pulumi
import pulumi_awsx as awsx
import pulumi_eks as eks
import pulumi_kubernetes as k8s
from pathlib import Path
import os

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

def apply_namespace(obj, opts):
    """
    Ensure all Kubernetes resources get deployed into the retailstore namespace.
    """
    if "metadata" in obj:
        metadata = obj["metadata"]
        if "namespace" not in metadata or metadata["namespace"] != namespace_name:
            metadata["namespace"] = namespace_name


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
    
    # Make the UI service a LoadBalancer
    if kind == "Service":
        metadata = obj.get("metadata", {})
        name = metadata.get("name", "")
        
        if "ui" in name.lower():
            spec = obj.get("spec", {})
            spec["type"] = "LoadBalancer"
            pulumi.log.info(f"Converting UI service '{name}' to LoadBalancer type")

# Services to deploy
services = ["cart", "catalog", "checkout", "orders", "ui"]

# Verify kompose files exist before proceeding
print("\n" + "="*60)
print("KOMPOSE FILES VERIFICATION")
print("="*60)

services_with_files = []
for service in services:
    kompose_files_dir = f"../src/{service}/kompose_files"
    abs_path = os.path.abspath(kompose_files_dir)
    
    print(f"\nChecking {service}:")
    print(f"  Path: {abs_path}")
    
    if Path(kompose_files_dir).exists():
        yaml_files = list(Path(kompose_files_dir).glob("*.yaml"))
        yml_files = list(Path(kompose_files_dir).glob("*.yml"))
        all_files = yaml_files + yml_files
        
        if all_files:
            print(f"  ✅ Found {len(all_files)} YAML file(s):")
            for f in all_files:
                print(f"    - {f.name}")
            services_with_files.append(service)
        else:
            print(f"  ❌ Directory exists but NO YAML files found!")
    else:
        print(f"  ❌ Directory does NOT exist!")
        print(f"  → Run: cd ../src/{service} && mkdir -p kompose_files && kompose convert --out kompose_files")

print("\n" + "="*60)
print(f"Summary: {len(services_with_files)}/{len(services)} services have kompose files")
print("="*60 + "\n")

if not services_with_files:
    pulumi.log.error("NO KOMPOSE FILES FOUND! Please run kompose convert first.")
    pulumi.log.error("Run this command:")
    pulumi.log.error("  cd ../src && for dir in cart catalog checkout orders ui; do (cd $dir && mkdir -p kompose_files && kompose convert --out kompose_files); done")

# Store the UI service for export
ui_service = None
deployed_count = 0

# Apply Kubernetes manifests for each service using v1 ConfigGroup (which supports transformations)
for service in services:
    kompose_files_dir = f"../src/{service}/kompose_files"
    
    # Check if the directory exists and has YAML files
    if Path(kompose_files_dir).exists():
        yaml_files = list(Path(kompose_files_dir).glob("*.yaml"))
        yml_files = list(Path(kompose_files_dir).glob("*.yml"))
        all_files = yaml_files + yml_files
        
        if all_files:
            # Use v1 ConfigGroup which supports transformations
            config_group = k8s.yaml.ConfigGroup(
                f"kompose-{service}",
                files=[f"{kompose_files_dir}/*.yaml"],
                transformations=[replace_images_with_ecr, apply_namespace],
                opts=pulumi.ResourceOptions(
                    provider=k8s_provider,
                    depends_on=[namespace] + list(built_images.values())
                )
            )
            
            deployed_count += 1
            
            # Capture the UI service for export
            if service == "ui":
                ui_service = config_group
            
            pulumi.log.info(f"✅ Applied Kubernetes manifests for {service} ({len(all_files)} files)")
        else:
            pulumi.log.warn(f"❌ Skipping {service}: Directory exists but no YAML files found")
    else:
        pulumi.log.warn(f"❌ Skipping {service}: Directory not found at {kompose_files_dir}")

pulumi.export("deployed_services", services_with_files)
pulumi.export("deployed_count", deployed_count)

