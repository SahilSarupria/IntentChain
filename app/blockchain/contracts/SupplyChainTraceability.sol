// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/**
 * @title SupplyChainTraceability
 * @notice On-chain product provenance & cold-chain checkpoint ledger.
 *
 * Use cases this backs (see IntentChain README):
 *   - Product Traceability: factory -> distributor -> retailer -> consumer,
 *     with every handoff logged immutably (fair-trade coffee, organic food,
 *     luxury goods anti-counterfeiting, etc).
 *   - Pharma & Cold-Chain: batches carry a temperature reading at every
 *     checkpoint, so a buyer (or regulator) can verify a drug batch was
 *     never outside its safe storage range.
 *
 * Deliberately dependency-free (no OpenZeppelin import) so it can be pasted
 * directly into Remix and deployed without a build pipeline. Access control
 * is a minimal role mapping — swap in OZ AccessControl for production use.
 */
contract SupplyChainTraceability {
    enum Role { None, Manufacturer, Distributor, Retailer, Auditor }

    struct Product {
        bytes32 id;
        string name;
        string origin;      // e.g. "Finca La Esperanza, Huila, Colombia"
        address manufacturer;
        uint256 registeredAt;
        bool exists;
    }

    struct Checkpoint {
        string location;
        string status;      // e.g. "In Transit", "Customs Cleared", "Delivered"
        int256 temperatureC; // x10 fixed point not required; whole degrees C is enough for demo
        address recordedBy;
        uint256 timestamp;
    }

    address public owner;
    mapping(address => Role) public roles;
    mapping(bytes32 => Product) private products;
    mapping(bytes32 => Checkpoint[]) private checkpoints;

    event RoleGranted(address indexed account, Role role);
    event ProductRegistered(bytes32 indexed productId, string name, address indexed manufacturer, uint256 timestamp);
    event CheckpointLogged(bytes32 indexed productId, string location, string status, int256 temperatureC, address indexed recordedBy, uint256 timestamp);

    modifier onlyOwner() {
        require(msg.sender == owner, "not owner");
        _;
    }

    modifier onlyRole(Role required) {
        require(roles[msg.sender] == required || msg.sender == owner, "unauthorized role");
        _;
    }

    modifier onlyKnownParty() {
        require(roles[msg.sender] != Role.None || msg.sender == owner, "unregistered party");
        _;
    }

    constructor() {
        owner = msg.sender;
        roles[msg.sender] = Role.Manufacturer;
    }

    function grantRole(address account, Role role) external onlyOwner {
        roles[account] = role;
        emit RoleGranted(account, role);
    }

    /// @notice Register a new product batch on-chain. `productId` is
    /// typically keccak256(sku + batch number) computed off-chain.
    function registerProduct(bytes32 productId, string calldata name, string calldata origin)
        external
        onlyRole(Role.Manufacturer)
    {
        require(!products[productId].exists, "product already registered");
        products[productId] = Product({
            id: productId,
            name: name,
            origin: origin,
            manufacturer: msg.sender,
            registeredAt: block.timestamp,
            exists: true
        });
        emit ProductRegistered(productId, name, msg.sender, block.timestamp);
    }

    /// @notice Log a handoff / condition checkpoint for an existing product.
    function logCheckpoint(bytes32 productId, string calldata location, string calldata status, int256 temperatureC)
        external
        onlyKnownParty
    {
        require(products[productId].exists, "unknown product");
        checkpoints[productId].push(Checkpoint({
            location: location,
            status: status,
            temperatureC: temperatureC,
            recordedBy: msg.sender,
            timestamp: block.timestamp
        }));
        emit CheckpointLogged(productId, location, status, temperatureC, msg.sender, block.timestamp);
    }

    function getProduct(bytes32 productId) external view returns (
        string memory name, string memory origin, address manufacturer, uint256 registeredAt, bool exists
    ) {
        Product storage p = products[productId];
        return (p.name, p.origin, p.manufacturer, p.registeredAt, p.exists);
    }

    function getCheckpointCount(bytes32 productId) external view returns (uint256) {
        return checkpoints[productId].length;
    }

    function getCheckpoint(bytes32 productId, uint256 index) external view returns (
        string memory location, string memory status, int256 temperatureC, address recordedBy, uint256 timestamp
    ) {
        Checkpoint storage c = checkpoints[productId][index];
        return (c.location, c.status, c.temperatureC, c.recordedBy, c.timestamp);
    }

    /// @notice Cheap authenticity check: does this product exist, who made
    /// it, and how many custody events does it have on record.
    function verifyAuthenticity(bytes32 productId) external view returns (
        bool exists, address manufacturer, uint256 checkpointCount
    ) {
        Product storage p = products[productId];
        return (p.exists, p.manufacturer, checkpoints[productId].length);
    }
}
