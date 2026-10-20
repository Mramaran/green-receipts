import { expect } from "chai";
import { network } from "hardhat";

const { ethers } = await network.connect();

const hash = (label) => ethers.keccak256(ethers.toUtf8Bytes(label));
const org = hash("acme");

describe("GreenReceipts", () => {
  let contract, admin, issuer, stranger;

  beforeEach(async () => {
    [admin, issuer, stranger] = await ethers.getSigners();
    contract = await ethers.deployContract("GreenReceipts", [admin.address, issuer.address]);
  });

  it("gives the issuer role only to the issuer", async () => {
    const role = await contract.ISSUER_ROLE();
    expect(await contract.hasRole(role, issuer.address)).to.equal(true);
    expect(await contract.hasRole(role, stranger.address)).to.equal(false);
  });

  it("records a receipt, updates the org totals and emits an event", async () => {
    const h = hash("receipt-1");
    await expect(contract.connect(issuer).recordReceipt(h, org, 240, 290))
      .to.emit(contract, "ReceiptRecorded")
      .withArgs(h, org, 240, 290, issuer.address);

    expect(await contract.isRecorded(h)).to.equal(true);
    expect(await contract.orgTotalMinNg(org)).to.equal(240);
    expect(await contract.orgTotalMaxNg(org)).to.equal(290);
    expect(await contract.orgReceiptCount(org)).to.equal(1);
    const record = await contract.getRecord(h);
    expect(record.orgId).to.equal(org);
    expect(record.gco2eMinNg).to.equal(240);
    expect(record.gco2eMaxNg).to.equal(290);
    expect(record.recordedAt).to.be.greaterThan(0);
    expect(record.issuer).to.equal(issuer.address);
  });

  it("keeps the recorded issuer after the issuer role is revoked", async () => {
    const role = await contract.ISSUER_ROLE();
    const h = hash("before-rotation");
    await contract.connect(issuer).recordReceipt(h, org, 5, 6);

    await contract.connect(admin).revokeRole(role, issuer.address);

    const record = await contract.getRecord(h);
    expect(record.issuer).to.equal(issuer.address);
    expect(await contract.isRecorded(h)).to.equal(true);
    await expect(contract.connect(issuer).recordReceipt(hash("after-rotation"), org, 5, 6))
      .to.be.revertedWithCustomError(contract, "AccessControlUnauthorizedAccount");
  });

  it("records the account that sent the transaction, not an account the caller names", async () => {
    const role = await contract.ISSUER_ROLE();
    await contract.connect(admin).grantRole(role, stranger.address);
    const h = hash("second-issuer");
    await contract.connect(stranger).recordReceipt(h, org, 1, 2);
    expect((await contract.getRecord(h)).issuer).to.equal(stranger.address);
  });

  it("lets the admin grant and revoke the issuer role", async () => {
    const role = await contract.ISSUER_ROLE();
    await contract.connect(admin).grantRole(role, stranger.address);
    await contract.connect(stranger).recordReceipt(hash("granted"), org, 1, 2);
    expect(await contract.isRecorded(hash("granted"))).to.equal(true);

    await contract.connect(admin).revokeRole(role, stranger.address);
    await expect(contract.connect(stranger).recordReceipt(hash("revoked"), org, 1, 2))
      .to.be.revertedWithCustomError(contract, "AccessControlUnauthorizedAccount");
  });

  it("accepts the largest range a uint128 can hold", async () => {
    const max = 2n ** 128n - 1n;
    await contract.connect(issuer).recordReceipt(hash("max"), org, max, max);
    expect(await contract.orgTotalMaxNg(org)).to.equal(max);
  });

  it("accumulates totals across receipts", async () => {
    await contract.connect(issuer).recordReceipt(hash("a"), org, 100, 150);
    await contract.connect(issuer).recordReceipt(hash("b"), org, 200, 260);
    expect(await contract.orgTotalMinNg(org)).to.equal(300);
    expect(await contract.orgTotalMaxNg(org)).to.equal(410);
    expect(await contract.orgReceiptCount(org)).to.equal(2);
  });

  it("rejects recording the same receipt twice", async () => {
    const h = hash("dup");
    await contract.connect(issuer).recordReceipt(h, org, 1, 2);
    await expect(contract.connect(issuer).recordReceipt(h, org, 1, 2))
      .to.be.revertedWithCustomError(contract, "AlreadyRecorded")
      .withArgs(h);
  });

  it("rejects callers without the issuer role", async () => {
    await expect(contract.connect(stranger).recordReceipt(hash("x"), org, 1, 2))
      .to.be.revertedWithCustomError(contract, "AccessControlUnauthorizedAccount");
  });

  it("rejects a range where min is above max", async () => {
    await expect(contract.connect(issuer).recordReceipt(hash("bad"), org, 5, 2))
      .to.be.revertedWithCustomError(contract, "InvalidRange");
  });

  it("reports an unrecorded hash as not recorded", async () => {
    expect(await contract.isRecorded(hash("never"))).to.equal(false);
  });
});
