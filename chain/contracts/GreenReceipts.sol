// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {AccessControl} from "@openzeppelin/contracts/access/AccessControl.sol";

/// @title GreenReceipts
/// @notice Append-only record of carbon receipts. Only the receipt hash and its emissions
///         range are stored; the full receipt stays off-chain and is checked against the hash.
///         Emissions are stored in nanograms CO2e so small per-request values stay integers.
contract GreenReceipts is AccessControl {
    bytes32 public constant ISSUER_ROLE = keccak256("ISSUER_ROLE");

    /// @dev `issuer` is the account that recorded the receipt. It is kept so a receipt stays
    ///      attributable after the issuer role is revoked or the key is rotated.
    struct Record {
        bytes32 orgId;
        uint64 recordedAt;
        address issuer;
        uint128 gco2eMinNg;
        uint128 gco2eMaxNg;
    }

    mapping(bytes32 => Record) private _records;
    mapping(bytes32 => uint256) public orgTotalMinNg;
    mapping(bytes32 => uint256) public orgTotalMaxNg;
    mapping(bytes32 => uint256) public orgReceiptCount;

    event ReceiptRecorded(
        bytes32 indexed receiptHash,
        bytes32 indexed orgId,
        uint128 gco2eMinNg,
        uint128 gco2eMaxNg,
        address issuer
    );

    error AlreadyRecorded(bytes32 receiptHash);
    error InvalidRange();

    constructor(address admin, address issuer) {
        _grantRole(DEFAULT_ADMIN_ROLE, admin);
        _grantRole(ISSUER_ROLE, issuer);
    }

    function recordReceipt(
        bytes32 receiptHash,
        bytes32 orgId,
        uint128 gco2eMinNg,
        uint128 gco2eMaxNg
    ) external onlyRole(ISSUER_ROLE) {
        if (gco2eMinNg > gco2eMaxNg) revert InvalidRange();
        if (_records[receiptHash].recordedAt != 0) revert AlreadyRecorded(receiptHash);

        _records[receiptHash] = Record(orgId, uint64(block.timestamp), msg.sender, gco2eMinNg, gco2eMaxNg);
        orgTotalMinNg[orgId] += gco2eMinNg;
        orgTotalMaxNg[orgId] += gco2eMaxNg;
        orgReceiptCount[orgId] += 1;

        emit ReceiptRecorded(receiptHash, orgId, gco2eMinNg, gco2eMaxNg, msg.sender);
    }

    function isRecorded(bytes32 receiptHash) external view returns (bool) {
        return _records[receiptHash].recordedAt != 0;
    }

    function getRecord(bytes32 receiptHash) external view returns (Record memory) {
        return _records[receiptHash];
    }
}
