// Copyright (c) 2025 Prajwal Shetty. All rights reserved.
// Licensed under the MIT License. See LICENSE file in the root directory of this
// source tree or http://opensource.org/licenses/MIT.
#pragma once

#include "CoreMinimal.h"
#include "Kismet/BlueprintFunctionLibrary.h"
#include "Engine/Blueprint.h"
#include "K2Node.h"
#include "GenBlueprintUtils.generated.h"

/**
 * Utility functions for Blueprint manipulation from AI/LLM commands
 */
UCLASS()
class GENERATIVEAISUPPORTEDITOR_API  UGenBlueprintUtils : public UBlueprintFunctionLibrary
{
	GENERATED_BODY()

public:
	/**
	 * Create a new Blueprint class
	 * 
	 * @param BlueprintName - Name for the new Blueprint
	 * @param ParentClassName - Parent class name or path
	 * @param SavePath - Directory path to save the Blueprint
	 * @return Newly created Blueprint, or nullptr if creation failed
	 */
	UFUNCTION(BlueprintCallable, Category = "Generative AI|Blueprint Utils")
	static UBlueprint* CreateBlueprint(const FString& BlueprintName, const FString& ParentClassName,
	                                   const FString& SavePath);

	/**
	 * Add a component to a Blueprint
	 * 
	 * @param BlueprintPath - Path to the Blueprint asset
	 * @param ComponentClass - Component class to add
	 * @param ComponentName - Name for the new component
	 * @return True if successful
	 */
	UFUNCTION(BlueprintCallable, Category = "Generative AI|Blueprint Utils")
	static bool AddComponent(const FString& BlueprintPath, const FString& ComponentClass, const FString& ComponentName);

	/**
	 * Add a variable to a Blueprint
	 * 
	 * @param BlueprintPath - Path to the Blueprint asset
	 * @param VariableName - Name for the new variable
	 * @param VariableType - Type of the variable as a string (e.g., "float", "vector")
	 * @param DefaultValue - Default value for the variable as a string
	 * @param Category - Category for organizing variables
	 * @return True if successful
	 */
	UFUNCTION(BlueprintCallable, Category = "Generative AI|Blueprint Utils")
	static bool AddVariable(const FString& BlueprintPath, const FString& VariableName,
	                        const FString& VariableType, const FString& DefaultValue,
	                        const FString& Category);

	/**
	 * Add a function to a Blueprint
	 * 
	 * @param BlueprintPath - Path to the Blueprint asset
	 * @param FunctionName - Name for the new function
	 * @param Inputs - Array of input parameter descriptions (JSON format)
	 * @param Outputs - Array of output parameter descriptions (JSON format)
	 * @return Function GUID as string if successful, empty string if failed
	 */
	UFUNCTION(BlueprintCallable, Category = "Generative AI|Blueprint Utils")
	static FString AddFunction(const FString& BlueprintPath, const FString& FunctionName,
	                           const FString& InputsJson, const FString& OutputsJson);

	/**
	 * Connect nodes in a Blueprint graph
	 * 
	 * @param BlueprintPath - Path to the Blueprint asset
	 * @param FunctionGuid - GUID of the function containing the nodes
	 * @param SourceNodeGuid - GUID of the source node
	 * @param SourcePinName - Name of the source pin
	 * @param TargetNodeGuid - GUID of the target node
	 * @param TargetPinName - Name of the target pin
	 * @return True if successful
	 */
	UFUNCTION(BlueprintCallable, Category = "Generative AI|Blueprint Utils")
	static FString ConnectNodes(const FString& BlueprintPath, const FString& FunctionGuid,
	                            const FString& SourceNodeGuid, const FString& SourcePinName,
	                            const FString& TargetNodeGuid, const FString& TargetPinName);

	/**
	 * Compile a Blueprint
	 * 
	 * @param BlueprintPath - Path to the Blueprint asset
	 * @return True if successful
	 */
	UFUNCTION(BlueprintCallable, Category = "Generative AI|Blueprint Utils")
	static bool CompileBlueprint(const FString& BlueprintPath);

	/**
	 * Spawn a Blueprint actor in the level
	 * 
	 * @param BlueprintPath - Path to the Blueprint asset
	 * @param Location - Spawn location
	 * @param Rotation - Spawn rotation
	 * @param Scale - Spawn scale
	 * @param ActorLabel - Optional label for the actor
	 * @return Spawned actor, or nullptr if spawn failed
	 */
	UFUNCTION(BlueprintCallable, Category = "Generative AI|Blueprint Utils")
	static AActor* SpawnBlueprint(const FString& BlueprintPath, const FVector& Location,
	                              const FRotator& Rotation, const FVector& Scale,
	                              const FString& ActorLabel);
	

	/**
	 * Connect multiple pairs of nodes in a Blueprint graph in a single operation
	 * 
	 * @param BlueprintPath - Path to the Blueprint asset
	 * @param FunctionGuid - GUID of the function containing the nodes
	 * @param ConnectionsJson - JSON array of connection definitions
	 * @return True if all connections were successful
	 */
	UFUNCTION(BlueprintCallable, Category = "Generative AI|Blueprint Utils")
	static FString ConnectNodesBulk(const FString& BlueprintPath, const FString& FunctionGuid,
	                                const FString& ConnectionsJson);
	
	static bool OpenBlueprintGraph(UBlueprint* Blueprint, UEdGraph* Graph = nullptr);
	
	UFUNCTION(BlueprintCallable, Category = "GenBlueprintUtils")
	static FString GetNodeGUID(const FString& BlueprintPath, const FString& GraphType, const FString& NodeName, const FString& FunctionGuid);
	
	UFUNCTION(BlueprintCallable, Category = "Blueprint")
	static FString AddComponentWithEvents(const FString& BlueprintPath, const FString& ComponentName,
	                               const FString& ComponentClassName);

	/** Return a JSON array describing every graph inside the Blueprint.
	 *  Shape: {"graphs":[{"path":"...","name":"...","kind":"..."}]} */
	UFUNCTION(BlueprintCallable, Category = "Generative AI|Blueprint Inspection")
	static FString GetAllGraphsJson(const FString& BlueprintPath);

	/** Resolve a graph by its hierarchical path (``EventGraph/MyFunction``).
	 *  Returns a JSON object: {"found":bool,"graph_path":"...","kind":"..."}. */
	UFUNCTION(BlueprintCallable, Category = "Generative AI|Blueprint Inspection")
	static FString ResolveGraphByPath(const FString& BlueprintPath, const FString& GraphPath);

	/** Return a JSON array of nodes inside the named graph. */
	UFUNCTION(BlueprintCallable, Category = "Generative AI|Blueprint Inspection")
	static FString GetGraphNodesJson(const FString& BlueprintPath, const FString& GraphPath);

	/** Return a JSON array of pins on the node whose GUID is ``NodeGuid``. */
	UFUNCTION(BlueprintCallable, Category = "Generative AI|Blueprint Inspection")
	static FString GetGraphPinsJson(const FString& BlueprintPath, const FString& GraphPath, const FString& NodeGuid);

	/** Resolve a node by a user-provided selector (GUID / name / event alias). */
	UFUNCTION(BlueprintCallable, Category = "Generative AI|Blueprint Inspection")
	static FString ResolveNodeBySelector(const FString& BlueprintPath, const FString& GraphPath,
	                                     const FString& Identifier, const FString& Kind);

	/** Report structured compile diagnostics. */
	UFUNCTION(BlueprintCallable, Category = "Generative AI|Blueprint Inspection")
	static FString CompileBlueprintWithDiagnostics(const FString& BlueprintPath);

private:
	// Helper functions for internal use
	static UBlueprint* LoadBlueprintAsset(const FString& BlueprintPath);
	static UClass* FindClassByName(const FString& ClassName);
	static UFunction* FindFunctionByName(UClass* Class, const FString& FunctionName);

	/** Return every graph inside a Blueprint by walking ``GetAllGraphs``. */
	static void CollectAllGraphs(UBlueprint* Blueprint, TArray<UEdGraph*>& OutGraphs);

	/** Find a graph by GUID across the full ``GetAllGraphs`` enumeration. */
	static UEdGraph* FindGraphByGuidAllGraphs(UBlueprint* Blueprint, const FGuid& GraphGuid);

	/** Find a graph by hierarchical path segments (case-insensitive names). */
	static UEdGraph* FindGraphByPath(UBlueprint* Blueprint, const FString& NormalizedGraphPath);

	/** Classify a graph into a stable display string (Ubergraph, Function, ...). */
	static FString ClassifyGraphKind(UBlueprint* Blueprint, UEdGraph* Graph);
};
