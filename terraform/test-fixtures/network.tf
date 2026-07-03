# Minimal free network plumbing for the billed fixtures below.

resource "aws_vpc" "fixtures" {
  cidr_block = "10.99.0.0/24"

  tags = { Name = "wasteless-fixtures" }
}

resource "aws_subnet" "a" {
  vpc_id            = aws_vpc.fixtures.id
  cidr_block        = "10.99.0.0/26"
  availability_zone = "${var.region}a"

  tags = { Name = "wasteless-fixtures-a" }
}

resource "aws_subnet" "b" {
  vpc_id            = aws_vpc.fixtures.id
  cidr_block        = "10.99.0.64/26"
  availability_zone = "${var.region}b"

  tags = { Name = "wasteless-fixtures-b" }
}
