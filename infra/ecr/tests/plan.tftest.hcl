mock_provider "aws" {}
run "immutable_encrypted_registry" {
  command = plan
  assert {
    condition     = aws_ecr_repository.app.image_tag_mutability == "IMMUTABLE" && !aws_ecr_repository.app.force_delete
    error_message = "Deployments must retain immutable image tags and avoid forced image deletion."
  }
  assert {
    condition     = aws_ecr_repository.app.encryption_configuration[0].encryption_type == "AES256"
    error_message = "Images must be encrypted at rest."
  }
}
