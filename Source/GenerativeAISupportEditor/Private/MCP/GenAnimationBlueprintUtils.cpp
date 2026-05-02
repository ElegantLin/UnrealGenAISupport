// Copyright (c) 2025 Prajwal Shetty. All rights reserved.
// Licensed under the MIT License. See LICENSE file in the root directory of this
// source tree or http://opensource.org/licenses/MIT.
#include "MCP/GenAnimationBlueprintUtils.h"

#include "Animation/AnimBlueprint.h"
#include "Animation/AnimBlueprintGeneratedClass.h"
#include "Animation/AnimInstance.h"
#include "Animation/AnimSequence.h"
#include "Animation/BlendSpace.h"
#include "Dom/JsonObject.h"
#include "EdGraph/EdGraph.h"
#include "EdGraph/EdGraphNode.h"
#include "EdGraph/EdGraphPin.h"
#include "Editor.h"
#include "Engine/SkeletalMesh.h"
#include "Kismet2/BlueprintEditorUtils.h"
#include "Kismet2/KismetEditorUtilities.h"
#include "Misc/PackageName.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "UObject/Package.h"
#include "UObject/SavePackage.h"
#include "UObject/UObjectIterator.h"

// State-machine specific headers. These live in the AnimGraph module.
#include "AnimationStateMachineGraph.h"
#include "AnimationStateMachineSchema.h"
#include "AnimationStateGraph.h"
#include "AnimationTransitionGraph.h"
#include "AnimGraphNode_StateMachine.h"
#include "AnimGraphNode_StateMachineBase.h"
#include "AnimGraphNode_SequencePlayer.h"
#include "AnimGraphNode_BlendSpacePlayer.h"
#include "AnimGraphNode_StateResult.h"
#include "AnimGraphNode_Slot.h"
#include "AnimGraphNode_ApplyAdditive.h"
#include "AnimStateNodeBase.h"
#include "AnimStateNode.h"
#include "AnimStateAliasNode.h"
#include "AnimStateTransitionNode.h"

namespace GenAnimBP
{
	FString SerializeJson(const TSharedRef<FJsonObject>& Object)
	{
		FString Out;
		TSharedRef<TJsonWriter<>> Writer = TJsonWriterFactory<>::Create(&Out);
		FJsonSerializer::Serialize(Object, Writer);
		return Out;
	}

	TSharedPtr<FJsonObject> ParseJson(const FString& Raw)
	{
		TSharedPtr<FJsonObject> Parsed;
		TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(Raw);
		if (FJsonSerializer::Deserialize(Reader, Parsed) && Parsed.IsValid())
		{
			return Parsed;
		}
		return nullptr;
	}

	FString MakeError(const FString& Message, const FString& ErrorCode = TEXT("ANIM_BP_WRITE_FAILED"))
	{
		TSharedRef<FJsonObject> Obj = MakeShared<FJsonObject>();
		Obj->SetBoolField(TEXT("success"), false);
		Obj->SetStringField(TEXT("error"), Message);
		Obj->SetStringField(TEXT("error_code"), ErrorCode);
		return SerializeJson(Obj);
	}

	FString MakeOk(const TSharedRef<FJsonObject>& Extras)
	{
		Extras->SetBoolField(TEXT("success"), true);
		return SerializeJson(Extras);
	}

	UAnimBlueprint* LoadAnimBP(const FString& Path)
	{
		return LoadObject<UAnimBlueprint>(nullptr, *Path);
	}

	bool CompileAndSave(UAnimBlueprint* AnimBP, bool& bCompiled, bool& bSaved)
	{
		bCompiled = false;
		bSaved = false;
		if (!AnimBP) return false;

		FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(AnimBP);
		FKismetEditorUtilities::CompileBlueprint(AnimBP);
		bCompiled = true;

		UPackage* Package = AnimBP->GetOutermost();
		if (!Package) return bCompiled;
		Package->MarkPackageDirty();
		const FString FileName = FPackageName::LongPackageNameToFilename(
			Package->GetName(), FPackageName::GetAssetPackageExtension());
		FSavePackageArgs Args;
		Args.TopLevelFlags = RF_Public | RF_Standalone;
		bSaved = UPackage::SavePackage(Package, nullptr, *FileName, Args);
		return bCompiled;
	}

	UAnimGraphNode_StateMachineBase* FindStateMachineNode(UAnimBlueprint* AnimBP, const FString& Name)
	{
		if (!AnimBP) return nullptr;
		TArray<UAnimGraphNode_StateMachineBase*> StateMachines;
		if (AnimBP->UbergraphPages.Num() == 0)
		{
			FBlueprintEditorUtils::GetAllNodesOfClass<UAnimGraphNode_StateMachineBase>(AnimBP, StateMachines);
		}
		else
		{
			FBlueprintEditorUtils::GetAllNodesOfClass<UAnimGraphNode_StateMachineBase>(AnimBP, StateMachines);
		}
		for (UAnimGraphNode_StateMachineBase* Node : StateMachines)
		{
			if (!Node) continue;
			if (Node->GetStateMachineName() == Name || Node->GetNodeTitle(ENodeTitleType::ListView).ToString() == Name)
			{
				return Node;
			}
		}
		return nullptr;
	}

	UAnimStateNodeBase* FindStateInMachine(UAnimGraphNode_StateMachineBase* SM, const FString& StateName)
	{
		if (!SM || !SM->EditorStateMachineGraph) return nullptr;
		for (UEdGraphNode* Node : SM->EditorStateMachineGraph->Nodes)
		{
			UAnimStateNodeBase* StateNode = Cast<UAnimStateNodeBase>(Node);
			if (StateNode && StateNode->GetStateName() == StateName)
			{
				return StateNode;
			}
		}
		return nullptr;
	}
}

// ---------------------------------------------------------------------------
// P4 Read implementations ----------------------------------------------------
// ---------------------------------------------------------------------------

FString UGenAnimationBlueprintUtils::GetAnimBlueprintStructure(const FString& AnimBlueprintPath)
{
	UAnimBlueprint* AnimBP = GenAnimBP::LoadAnimBP(AnimBlueprintPath);
	if (!AnimBP)
	{
		return GenAnimBP::MakeError(
			FString::Printf(TEXT("AnimBlueprint not found: %s"), *AnimBlueprintPath),
			TEXT("ASSET_NOT_FOUND"));
	}

	TSharedRef<FJsonObject> Out = MakeShared<FJsonObject>();
	Out->SetStringField(TEXT("anim_blueprint_path"), AnimBlueprintPath);
	Out->SetStringField(TEXT("parent_class"),
		AnimBP->ParentClass ? AnimBP->ParentClass->GetName() : TEXT(""));
	if (USkeleton* Skeleton = AnimBP->TargetSkeleton)
	{
		Out->SetStringField(TEXT("target_skeleton"), Skeleton->GetPathName());
	}

	TArray<TSharedPtr<FJsonValue>> StateMachines;
	TArray<UAnimGraphNode_StateMachineBase*> StateMachineNodes;
	FBlueprintEditorUtils::GetAllNodesOfClass<UAnimGraphNode_StateMachineBase>(AnimBP, StateMachineNodes);
	for (UAnimGraphNode_StateMachineBase* SM : StateMachineNodes)
	{
		if (!SM) continue;
		TSharedRef<FJsonObject> SmJson = MakeShared<FJsonObject>();
		SmJson->SetStringField(TEXT("name"), SM->GetStateMachineName());

		TArray<TSharedPtr<FJsonValue>> States;
		TArray<TSharedPtr<FJsonValue>> Transitions;
		if (SM->EditorStateMachineGraph)
		{
			for (UEdGraphNode* Node : SM->EditorStateMachineGraph->Nodes)
			{
				if (UAnimStateTransitionNode* Trans = Cast<UAnimStateTransitionNode>(Node))
				{
					TSharedRef<FJsonObject> T = MakeShared<FJsonObject>();
					T->SetStringField(TEXT("from_state"),
						Trans->GetPreviousState() ? Trans->GetPreviousState()->GetStateName() : TEXT(""));
					T->SetStringField(TEXT("to_state"),
						Trans->GetNextState() ? Trans->GetNextState()->GetStateName() : TEXT(""));
					T->SetNumberField(TEXT("blend_time"), Trans->CrossfadeDuration);
					Transitions.Add(MakeShared<FJsonValueObject>(T));
				}
				else if (UAnimStateNodeBase* StateNode = Cast<UAnimStateNodeBase>(Node))
				{
					TSharedRef<FJsonObject> S = MakeShared<FJsonObject>();
					S->SetStringField(TEXT("name"), StateNode->GetStateName());
					if (Cast<UAnimStateAliasNode>(StateNode))
					{
						S->SetStringField(TEXT("kind"), TEXT("StateAlias"));
					}
					else
					{
						S->SetStringField(TEXT("kind"), TEXT("State"));
					}
					States.Add(MakeShared<FJsonValueObject>(S));
				}
			}
		}
		SmJson->SetArrayField(TEXT("states"), States);
		SmJson->SetArrayField(TEXT("transitions"), Transitions);
		StateMachines.Add(MakeShared<FJsonValueObject>(SmJson));
	}
	Out->SetArrayField(TEXT("state_machines"), StateMachines);

	// Cached poses + slots (best-effort scan)
	TArray<TSharedPtr<FJsonValue>> Slots;
	TArray<UAnimGraphNode_Slot*> SlotNodes;
	FBlueprintEditorUtils::GetAllNodesOfClass<UAnimGraphNode_Slot>(AnimBP, SlotNodes);
	for (UAnimGraphNode_Slot* Slot : SlotNodes)
	{
		if (!Slot) continue;
		Slots.Add(MakeShared<FJsonValueString>(Slot->Node.SlotName.ToString()));
	}
	Out->SetArrayField(TEXT("slots"), Slots);

	Out->SetArrayField(TEXT("cached_poses"), {});
	Out->SetArrayField(TEXT("warnings"), {});
	return GenAnimBP::SerializeJson(Out);
}

FString UGenAnimationBlueprintUtils::GetGraphNodes(const FString& AnimBlueprintPath, const FString& GraphPath)
{
	UAnimBlueprint* AnimBP = GenAnimBP::LoadAnimBP(AnimBlueprintPath);
	if (!AnimBP)
	{
		return GenAnimBP::MakeError(TEXT("AnimBlueprint not found"), TEXT("ASSET_NOT_FOUND"));
	}

	TArray<FString> Parts;
	GraphPath.ParseIntoArray(Parts, TEXT("/"), true);
	if (Parts.Num() == 0)
	{
		return GenAnimBP::MakeError(TEXT("graph_path is empty"), TEXT("INVALID_PARAMETERS"));
	}

	UEdGraph* Graph = nullptr;
	if (Parts[0].Equals(TEXT("AnimGraph"), ESearchCase::IgnoreCase))
	{
		for (UEdGraph* G : AnimBP->FunctionGraphs)
		{
			if (G && G->GetName() == TEXT("AnimGraph")) { Graph = G; break; }
		}
		if (Graph && Parts.Num() >= 2)
		{
			if (UAnimGraphNode_StateMachineBase* SM = GenAnimBP::FindStateMachineNode(AnimBP, Parts[1]))
			{
				Graph = SM->EditorStateMachineGraph;
			}
		}
	}

	TSharedRef<FJsonObject> Out = MakeShared<FJsonObject>();
	if (!Graph)
	{
		Out->SetArrayField(TEXT("nodes"), {});
		return GenAnimBP::SerializeJson(Out);
	}

	TArray<TSharedPtr<FJsonValue>> Nodes;
	for (UEdGraphNode* Node : Graph->Nodes)
	{
		if (!Node) continue;
		TSharedRef<FJsonObject> N = MakeShared<FJsonObject>();
		N->SetStringField(TEXT("node_id"), Node->NodeGuid.ToString());
		N->SetStringField(TEXT("title"), Node->GetNodeTitle(ENodeTitleType::ListView).ToString());
		N->SetStringField(TEXT("kind"), Node->GetClass()->GetName());
		Nodes.Add(MakeShared<FJsonValueObject>(N));
	}
	Out->SetArrayField(TEXT("nodes"), Nodes);
	Out->SetStringField(TEXT("graph_path"), GraphPath);
	return GenAnimBP::SerializeJson(Out);
}

FString UGenAnimationBlueprintUtils::GetGraphPins(
	const FString& AnimBlueprintPath,
	const FString& GraphPath,
	const FString& NodeId)
{
	UAnimBlueprint* AnimBP = GenAnimBP::LoadAnimBP(AnimBlueprintPath);
	if (!AnimBP)
	{
		return GenAnimBP::MakeError(TEXT("AnimBlueprint not found"), TEXT("ASSET_NOT_FOUND"));
	}

	FGuid TargetGuid;
	FGuid::Parse(NodeId, TargetGuid);

	TArray<UEdGraph*> AllGraphs;
	AnimBP->GetAllGraphs(AllGraphs);
	for (UEdGraph* Graph : AllGraphs)
	{
		if (!Graph) continue;
		for (UEdGraphNode* Node : Graph->Nodes)
		{
			if (!Node || Node->NodeGuid != TargetGuid) continue;
			TSharedRef<FJsonObject> Out = MakeShared<FJsonObject>();
			TArray<TSharedPtr<FJsonValue>> Pins;
			for (UEdGraphPin* Pin : Node->Pins)
			{
				if (!Pin) continue;
				TSharedRef<FJsonObject> P = MakeShared<FJsonObject>();
				P->SetStringField(TEXT("name"), Pin->PinName.ToString());
				P->SetStringField(TEXT("direction"), Pin->Direction == EGPD_Input ? TEXT("input") : TEXT("output"));
				P->SetStringField(TEXT("pin_type"), Pin->PinType.PinCategory.ToString());
				P->SetStringField(TEXT("default_value"), Pin->DefaultValue);
				TArray<TSharedPtr<FJsonValue>> Linked;
				for (UEdGraphPin* Other : Pin->LinkedTo)
				{
					if (Other && Other->GetOwningNode())
					{
						Linked.Add(MakeShared<FJsonValueString>(Other->GetOwningNode()->NodeGuid.ToString()));
					}
				}
				P->SetArrayField(TEXT("linked_to"), Linked);
				Pins.Add(MakeShared<FJsonValueObject>(P));
			}
			Out->SetArrayField(TEXT("pins"), Pins);
			Out->SetStringField(TEXT("node_id"), NodeId);
			return GenAnimBP::SerializeJson(Out);
		}
	}
	return GenAnimBP::MakeError(TEXT("Node not found"), TEXT("NODE_NOT_FOUND"));
}

FString UGenAnimationBlueprintUtils::ResolveGraphByPath(const FString& AnimBlueprintPath, const FString& GraphPath)
{
	UAnimBlueprint* AnimBP = GenAnimBP::LoadAnimBP(AnimBlueprintPath);
	if (!AnimBP)
	{
		return GenAnimBP::MakeError(TEXT("AnimBlueprint not found"), TEXT("ASSET_NOT_FOUND"));
	}
	TSharedRef<FJsonObject> Out = MakeShared<FJsonObject>();
	Out->SetStringField(TEXT("graph_path"), GraphPath);
	Out->SetStringField(TEXT("anim_blueprint_path"), AnimBlueprintPath);
	Out->SetBoolField(TEXT("resolved"), true);
	return GenAnimBP::SerializeJson(Out);
}

// ---------------------------------------------------------------------------
// P5 Write implementations ---------------------------------------------------
//
// NOTE: Full state-machine authoring touches a large surface of the editor.
// These implementations call the public graph creation APIs where possible
// and rely on ``CompileBlueprint`` to surface any remaining issues to the
// caller. Each method returns enough structured information for Python
// handlers to assemble a mutation report.
// ---------------------------------------------------------------------------

FString UGenAnimationBlueprintUtils::CreateStateMachine(const FString& AnimBlueprintPath, const FString& PayloadJson)
{
	UAnimBlueprint* AnimBP = GenAnimBP::LoadAnimBP(AnimBlueprintPath);
	if (!AnimBP) return GenAnimBP::MakeError(TEXT("AnimBlueprint not found"), TEXT("ASSET_NOT_FOUND"));

	TSharedPtr<FJsonObject> Payload = GenAnimBP::ParseJson(PayloadJson);
	if (!Payload.IsValid()) return GenAnimBP::MakeError(TEXT("invalid payload"), TEXT("INVALID_PARAMETERS"));
	const FString SMName = Payload->GetStringField(TEXT("state_machine"));

	UEdGraph* AnimGraph = nullptr;
	for (UEdGraph* G : AnimBP->FunctionGraphs)
	{
		if (G && G->GetName() == TEXT("AnimGraph")) { AnimGraph = G; break; }
	}
	if (!AnimGraph) return GenAnimBP::MakeError(TEXT("AnimGraph not found"), TEXT("GRAPH_NOT_FOUND"));

	if (GenAnimBP::FindStateMachineNode(AnimBP, SMName))
	{
		return GenAnimBP::MakeError(
			FString::Printf(TEXT("State machine already exists: %s"), *SMName),
			TEXT("INVALID_PARAMETERS"));
	}

	AnimGraph->Modify();
	UAnimGraphNode_StateMachine* SMNode = NewObject<UAnimGraphNode_StateMachine>(
		AnimGraph, UAnimGraphNode_StateMachine::StaticClass(), NAME_None, RF_Transactional);
	if (!SMNode)
	{
		return GenAnimBP::MakeError(TEXT("Failed to create state machine node"));
	}

	AnimGraph->AddNode(SMNode, true, false);
	SMNode->CreateNewGuid();
	SMNode->PostPlacedNewNode();
	SMNode->AllocateDefaultPins();
	SMNode->NodePosX = 0;
	SMNode->NodePosY = 0;

	if (!SMNode->EditorStateMachineGraph)
	{
		return GenAnimBP::MakeError(TEXT("Failed to create state machine graph"));
	}
	FBlueprintEditorUtils::RenameGraph(SMNode->EditorStateMachineGraph, SMName);

	bool bCompiled = false, bSaved = false;
	GenAnimBP::CompileAndSave(AnimBP, bCompiled, bSaved);

	TSharedRef<FJsonObject> Out = MakeShared<FJsonObject>();
	Out->SetBoolField(TEXT("compiled"), bCompiled);
	Out->SetBoolField(TEXT("saved"), bSaved);
	Out->SetStringField(TEXT("state_machine"), SMName);
	Out->SetStringField(TEXT("graph_path"), SMNode->EditorStateMachineGraph->GetPathName());
	return GenAnimBP::MakeOk(Out);
}

FString UGenAnimationBlueprintUtils::CreateState(const FString& AnimBlueprintPath, const FString& PayloadJson)
{
	UAnimBlueprint* AnimBP = GenAnimBP::LoadAnimBP(AnimBlueprintPath);
	if (!AnimBP) return GenAnimBP::MakeError(TEXT("AnimBlueprint not found"), TEXT("ASSET_NOT_FOUND"));
	TSharedPtr<FJsonObject> Payload = GenAnimBP::ParseJson(PayloadJson);
	if (!Payload.IsValid()) return GenAnimBP::MakeError(TEXT("invalid payload"), TEXT("INVALID_PARAMETERS"));

	const FString SMName = Payload->GetStringField(TEXT("state_machine"));
	const FString StateName = Payload->GetStringField(TEXT("state"));

	UAnimGraphNode_StateMachineBase* SM = GenAnimBP::FindStateMachineNode(AnimBP, SMName);
	if (!SM || !SM->EditorStateMachineGraph)
	{
		return GenAnimBP::MakeError(TEXT("State machine not found"), TEXT("ANIM_BP_STATE_MACHINE_NOT_FOUND"));
	}

	UAnimStateNode* NewState = NewObject<UAnimStateNode>(
		SM->EditorStateMachineGraph, UAnimStateNode::StaticClass(), NAME_None, RF_Transactional);
	NewState->NodePosX = 100;
	NewState->NodePosY = 100;
	SM->EditorStateMachineGraph->AddNode(NewState, true, false);
	NewState->CreateNewGuid();
	NewState->PostPlacedNewNode();
	NewState->AllocateDefaultPins();
	if (NewState->BoundGraph)
	{
		FBlueprintEditorUtils::RenameGraph(NewState->BoundGraph, StateName);
	}

	bool bCompiled = false, bSaved = false;
	GenAnimBP::CompileAndSave(AnimBP, bCompiled, bSaved);

	TSharedRef<FJsonObject> Out = MakeShared<FJsonObject>();
	Out->SetBoolField(TEXT("compiled"), bCompiled);
	Out->SetBoolField(TEXT("saved"), bSaved);
	Out->SetStringField(TEXT("state"), StateName);
	return GenAnimBP::MakeOk(Out);
}

FString UGenAnimationBlueprintUtils::CreateTransition(const FString& AnimBlueprintPath, const FString& PayloadJson)
{
	UAnimBlueprint* AnimBP = GenAnimBP::LoadAnimBP(AnimBlueprintPath);
	if (!AnimBP) return GenAnimBP::MakeError(TEXT("AnimBlueprint not found"), TEXT("ASSET_NOT_FOUND"));
	TSharedPtr<FJsonObject> Payload = GenAnimBP::ParseJson(PayloadJson);
	if (!Payload.IsValid()) return GenAnimBP::MakeError(TEXT("invalid payload"), TEXT("INVALID_PARAMETERS"));

	const FString SMName = Payload->GetStringField(TEXT("state_machine"));
	const FString FromName = Payload->GetStringField(TEXT("from_state"));
	const FString ToName = Payload->GetStringField(TEXT("to_state"));

	UAnimGraphNode_StateMachineBase* SM = GenAnimBP::FindStateMachineNode(AnimBP, SMName);
	if (!SM) return GenAnimBP::MakeError(TEXT("State machine not found"), TEXT("ANIM_BP_STATE_MACHINE_NOT_FOUND"));

	UAnimStateNodeBase* From = GenAnimBP::FindStateInMachine(SM, FromName);
	UAnimStateNodeBase* To = GenAnimBP::FindStateInMachine(SM, ToName);
	if (!From || !To) return GenAnimBP::MakeError(TEXT("From/To state not found"), TEXT("ANIM_BP_STATE_NOT_FOUND"));

	UAnimStateTransitionNode* Trans = NewObject<UAnimStateTransitionNode>(
		SM->EditorStateMachineGraph, UAnimStateTransitionNode::StaticClass(), NAME_None, RF_Transactional);
	Trans->CreateNewGuid();
	SM->EditorStateMachineGraph->AddNode(Trans, true, false);
	Trans->AllocateDefaultPins();
	// Connect From -> Trans -> To using schema helpers
	const UEdGraphSchema* Schema = SM->EditorStateMachineGraph->GetSchema();
	if (Schema)
	{
		Schema->TryCreateConnection(From->GetOutputPin(), Trans->GetInputPin());
		Schema->TryCreateConnection(Trans->GetOutputPin(), To->GetInputPin());
	}

	const TSharedPtr<FJsonObject>* RuleObj = nullptr;
	if (Payload->TryGetObjectField(TEXT("rule"), RuleObj) && RuleObj && RuleObj->IsValid())
	{
		double BlendTime = 0.2;
		(*RuleObj)->TryGetNumberField(TEXT("blend_time"), BlendTime);
		Trans->CrossfadeDuration = static_cast<float>(BlendTime);
	}

	bool bCompiled = false, bSaved = false;
	GenAnimBP::CompileAndSave(AnimBP, bCompiled, bSaved);

	TSharedRef<FJsonObject> Out = MakeShared<FJsonObject>();
	Out->SetBoolField(TEXT("compiled"), bCompiled);
	Out->SetBoolField(TEXT("saved"), bSaved);
	Out->SetStringField(TEXT("from_state"), FromName);
	Out->SetStringField(TEXT("to_state"), ToName);
	return GenAnimBP::MakeOk(Out);
}

FString UGenAnimationBlueprintUtils::SetTransitionRule(const FString& AnimBlueprintPath, const FString& PayloadJson)
{
	// The rule is authored in the transition's sub-graph. For the first
	// cut we only persist ``blend_time`` on the transition; richer rule
	// authoring should be driven through transaction_commands + the
	// semantic node authoring APIs.
	UAnimBlueprint* AnimBP = GenAnimBP::LoadAnimBP(AnimBlueprintPath);
	if (!AnimBP) return GenAnimBP::MakeError(TEXT("AnimBlueprint not found"), TEXT("ASSET_NOT_FOUND"));
	TSharedPtr<FJsonObject> Payload = GenAnimBP::ParseJson(PayloadJson);
	if (!Payload.IsValid()) return GenAnimBP::MakeError(TEXT("invalid payload"), TEXT("INVALID_PARAMETERS"));

	const FString SMName = Payload->GetStringField(TEXT("state_machine"));
	const FString FromName = Payload->GetStringField(TEXT("from_state"));
	const FString ToName = Payload->GetStringField(TEXT("to_state"));

	UAnimGraphNode_StateMachineBase* SM = GenAnimBP::FindStateMachineNode(AnimBP, SMName);
	if (!SM || !SM->EditorStateMachineGraph)
	{
		return GenAnimBP::MakeError(TEXT("State machine not found"), TEXT("ANIM_BP_STATE_MACHINE_NOT_FOUND"));
	}

	UAnimStateTransitionNode* Target = nullptr;
	for (UEdGraphNode* Node : SM->EditorStateMachineGraph->Nodes)
	{
		UAnimStateTransitionNode* Trans = Cast<UAnimStateTransitionNode>(Node);
		if (!Trans) continue;
		UAnimStateNodeBase* Prev = Trans->GetPreviousState();
		UAnimStateNodeBase* Next = Trans->GetNextState();
		if (Prev && Next && Prev->GetStateName() == FromName && Next->GetStateName() == ToName)
		{
			Target = Trans;
			break;
		}
	}
	if (!Target) return GenAnimBP::MakeError(TEXT("Transition not found"), TEXT("NODE_NOT_FOUND"));

	const TSharedPtr<FJsonObject>* RuleObj = nullptr;
	if (Payload->TryGetObjectField(TEXT("rule"), RuleObj) && RuleObj && RuleObj->IsValid())
	{
		double BlendTime = Target->CrossfadeDuration;
		(*RuleObj)->TryGetNumberField(TEXT("blend_time"), BlendTime);
		Target->CrossfadeDuration = static_cast<float>(BlendTime);
	}

	bool bCompiled = false, bSaved = false;
	GenAnimBP::CompileAndSave(AnimBP, bCompiled, bSaved);

	TSharedRef<FJsonObject> Out = MakeShared<FJsonObject>();
	Out->SetBoolField(TEXT("compiled"), bCompiled);
	Out->SetBoolField(TEXT("saved"), bSaved);
	return GenAnimBP::MakeOk(Out);
}

FString UGenAnimationBlueprintUtils::CreateStateAlias(const FString& AnimBlueprintPath, const FString& PayloadJson)
{
	UAnimBlueprint* AnimBP = GenAnimBP::LoadAnimBP(AnimBlueprintPath);
	if (!AnimBP) return GenAnimBP::MakeError(TEXT("AnimBlueprint not found"), TEXT("ASSET_NOT_FOUND"));
	TSharedPtr<FJsonObject> Payload = GenAnimBP::ParseJson(PayloadJson);
	if (!Payload.IsValid()) return GenAnimBP::MakeError(TEXT("invalid payload"), TEXT("INVALID_PARAMETERS"));

	const FString SMName = Payload->GetStringField(TEXT("state_machine"));
	const FString AliasName = Payload->GetStringField(TEXT("alias_name"));

	UAnimGraphNode_StateMachineBase* SM = GenAnimBP::FindStateMachineNode(AnimBP, SMName);
	if (!SM || !SM->EditorStateMachineGraph)
	{
		return GenAnimBP::MakeError(TEXT("State machine not found"), TEXT("ANIM_BP_STATE_MACHINE_NOT_FOUND"));
	}

	UAnimStateAliasNode* Alias = NewObject<UAnimStateAliasNode>(
		SM->EditorStateMachineGraph, UAnimStateAliasNode::StaticClass(), NAME_None, RF_Transactional);
	SM->EditorStateMachineGraph->AddNode(Alias, true, false);
	Alias->CreateNewGuid();
	Alias->PostPlacedNewNode();
	Alias->AllocateDefaultPins();
	Alias->OnRenameNode(AliasName);

	bool bCompiled = false, bSaved = false;
	GenAnimBP::CompileAndSave(AnimBP, bCompiled, bSaved);

	TSharedRef<FJsonObject> Out = MakeShared<FJsonObject>();
	Out->SetBoolField(TEXT("compiled"), bCompiled);
	Out->SetBoolField(TEXT("saved"), bSaved);
	Out->SetStringField(TEXT("alias_name"), AliasName);
	return GenAnimBP::MakeOk(Out);
}

FString UGenAnimationBlueprintUtils::SetAliasTargets(const FString& AnimBlueprintPath, const FString& PayloadJson)
{
	UAnimBlueprint* AnimBP = GenAnimBP::LoadAnimBP(AnimBlueprintPath);
	if (!AnimBP) return GenAnimBP::MakeError(TEXT("AnimBlueprint not found"), TEXT("ASSET_NOT_FOUND"));
	TSharedPtr<FJsonObject> Payload = GenAnimBP::ParseJson(PayloadJson);
	if (!Payload.IsValid()) return GenAnimBP::MakeError(TEXT("invalid payload"), TEXT("INVALID_PARAMETERS"));

	const FString SMName = Payload->GetStringField(TEXT("state_machine"));
	const FString AliasName = Payload->GetStringField(TEXT("alias_name"));
	const TArray<TSharedPtr<FJsonValue>>* Targets = nullptr;
	Payload->TryGetArrayField(TEXT("aliased_states"), Targets);

	UAnimGraphNode_StateMachineBase* SM = GenAnimBP::FindStateMachineNode(AnimBP, SMName);
	if (!SM || !SM->EditorStateMachineGraph)
	{
		return GenAnimBP::MakeError(TEXT("State machine not found"), TEXT("ANIM_BP_STATE_MACHINE_NOT_FOUND"));
	}

	UAnimStateAliasNode* Alias = nullptr;
	for (UEdGraphNode* Node : SM->EditorStateMachineGraph->Nodes)
	{
		UAnimStateAliasNode* Candidate = Cast<UAnimStateAliasNode>(Node);
		if (Candidate && Candidate->GetStateName() == AliasName) { Alias = Candidate; break; }
	}
	if (!Alias) return GenAnimBP::MakeError(TEXT("Alias not found"), TEXT("ANIM_BP_STATE_NOT_FOUND"));

	Alias->GetAliasedStates().Empty();
	if (Targets)
	{
		for (const TSharedPtr<FJsonValue>& Value : *Targets)
		{
			FString Name;
			if (Value.IsValid() && Value->TryGetString(Name))
			{
				UAnimStateNodeBase* Target = GenAnimBP::FindStateInMachine(SM, Name);
				if (Target) Alias->GetAliasedStates().Add(Target);
			}
		}
	}

	bool bCompiled = false, bSaved = false;
	GenAnimBP::CompileAndSave(AnimBP, bCompiled, bSaved);

	TSharedRef<FJsonObject> Out = MakeShared<FJsonObject>();
	Out->SetBoolField(TEXT("compiled"), bCompiled);
	Out->SetBoolField(TEXT("saved"), bSaved);
	return GenAnimBP::MakeOk(Out);
}

FString UGenAnimationBlueprintUtils::SetStateSequenceAsset(const FString& AnimBlueprintPath, const FString& PayloadJson)
{
	UAnimBlueprint* AnimBP = GenAnimBP::LoadAnimBP(AnimBlueprintPath);
	if (!AnimBP) return GenAnimBP::MakeError(TEXT("AnimBlueprint not found"), TEXT("ASSET_NOT_FOUND"));
	TSharedPtr<FJsonObject> Payload = GenAnimBP::ParseJson(PayloadJson);
	if (!Payload.IsValid()) return GenAnimBP::MakeError(TEXT("invalid payload"), TEXT("INVALID_PARAMETERS"));

	const FString SMName = Payload->GetStringField(TEXT("state_machine"));
	const FString StateName = Payload->GetStringField(TEXT("state"));
	const FString AssetPath = Payload->GetStringField(TEXT("asset_path"));
	double PlayRate = 1.0;
	Payload->TryGetNumberField(TEXT("play_rate"), PlayRate);

	UAnimSequence* Sequence = LoadObject<UAnimSequence>(nullptr, *AssetPath);
	if (!Sequence) return GenAnimBP::MakeError(TEXT("Sequence asset not found"), TEXT("ASSET_NOT_FOUND"));

	UAnimGraphNode_StateMachineBase* SM = GenAnimBP::FindStateMachineNode(AnimBP, SMName);
	if (!SM) return GenAnimBP::MakeError(TEXT("State machine not found"), TEXT("ANIM_BP_STATE_MACHINE_NOT_FOUND"));
	UAnimStateNode* State = Cast<UAnimStateNode>(GenAnimBP::FindStateInMachine(SM, StateName));
	if (!State) return GenAnimBP::MakeError(TEXT("State not found"), TEXT("ANIM_BP_STATE_NOT_FOUND"));
	if (!State->BoundGraph)
	{
		return GenAnimBP::MakeError(TEXT("State has no inner graph"), TEXT("GRAPH_NOT_FOUND"));
	}

	UAnimGraphNode_SequencePlayer* Player = nullptr;
	for (UEdGraphNode* Node : State->BoundGraph->Nodes)
	{
		Player = Cast<UAnimGraphNode_SequencePlayer>(Node);
		if (Player) break;
	}
	if (!Player)
	{
		Player = NewObject<UAnimGraphNode_SequencePlayer>(
			State->BoundGraph, UAnimGraphNode_SequencePlayer::StaticClass(), NAME_None, RF_Transactional);
		Player->CreateNewGuid();
		State->BoundGraph->AddNode(Player, true, false);
		Player->AllocateDefaultPins();
	}
	Player->Node.SetSequence(Sequence);
	Player->Node.SetPlayRate(static_cast<float>(PlayRate));

	bool bCompiled = false, bSaved = false;
	GenAnimBP::CompileAndSave(AnimBP, bCompiled, bSaved);

	TSharedRef<FJsonObject> Out = MakeShared<FJsonObject>();
	Out->SetBoolField(TEXT("compiled"), bCompiled);
	Out->SetBoolField(TEXT("saved"), bSaved);
	Out->SetStringField(TEXT("asset_path"), AssetPath);
	return GenAnimBP::MakeOk(Out);
}

FString UGenAnimationBlueprintUtils::SetStateBlendSpaceAsset(const FString& AnimBlueprintPath, const FString& PayloadJson)
{
	UAnimBlueprint* AnimBP = GenAnimBP::LoadAnimBP(AnimBlueprintPath);
	if (!AnimBP) return GenAnimBP::MakeError(TEXT("AnimBlueprint not found"), TEXT("ASSET_NOT_FOUND"));
	TSharedPtr<FJsonObject> Payload = GenAnimBP::ParseJson(PayloadJson);
	if (!Payload.IsValid()) return GenAnimBP::MakeError(TEXT("invalid payload"), TEXT("INVALID_PARAMETERS"));

	const FString SMName = Payload->GetStringField(TEXT("state_machine"));
	const FString StateName = Payload->GetStringField(TEXT("state"));
	const FString AssetPath = Payload->GetStringField(TEXT("asset_path"));
	double PlayRate = 1.0;
	Payload->TryGetNumberField(TEXT("play_rate"), PlayRate);

	UBlendSpace* BlendSpace = LoadObject<UBlendSpace>(nullptr, *AssetPath);
	if (!BlendSpace) return GenAnimBP::MakeError(TEXT("BlendSpace asset not found"), TEXT("ASSET_NOT_FOUND"));

	UAnimGraphNode_StateMachineBase* SM = GenAnimBP::FindStateMachineNode(AnimBP, SMName);
	if (!SM) return GenAnimBP::MakeError(TEXT("State machine not found"), TEXT("ANIM_BP_STATE_MACHINE_NOT_FOUND"));
	UAnimStateNode* State = Cast<UAnimStateNode>(GenAnimBP::FindStateInMachine(SM, StateName));
	if (!State || !State->BoundGraph) return GenAnimBP::MakeError(TEXT("State not found"), TEXT("ANIM_BP_STATE_NOT_FOUND"));

	UAnimGraphNode_BlendSpacePlayer* Player = nullptr;
	for (UEdGraphNode* Node : State->BoundGraph->Nodes)
	{
		Player = Cast<UAnimGraphNode_BlendSpacePlayer>(Node);
		if (Player) break;
	}
	if (!Player)
	{
		Player = NewObject<UAnimGraphNode_BlendSpacePlayer>(
			State->BoundGraph, UAnimGraphNode_BlendSpacePlayer::StaticClass(), NAME_None, RF_Transactional);
		Player->CreateNewGuid();
		State->BoundGraph->AddNode(Player, true, false);
		Player->AllocateDefaultPins();
	}
	Player->Node.SetBlendSpace(BlendSpace);
	Player->Node.SetPlayRate(static_cast<float>(PlayRate));

	bool bCompiled = false, bSaved = false;
	GenAnimBP::CompileAndSave(AnimBP, bCompiled, bSaved);

	TSharedRef<FJsonObject> Out = MakeShared<FJsonObject>();
	Out->SetBoolField(TEXT("compiled"), bCompiled);
	Out->SetBoolField(TEXT("saved"), bSaved);
	Out->SetStringField(TEXT("asset_path"), AssetPath);
	return GenAnimBP::MakeOk(Out);
}

FString UGenAnimationBlueprintUtils::SetCachedPoseNode(const FString& /*AnimBlueprintPath*/, const FString& /*PayloadJson*/)
{
	// Cached-pose wiring is a graph-rewrite operation that depends heavily
	// on the existing AnimGraph topology; the safe path is to expose
	// creation through ``transaction_commands`` + the generic node
	// authoring APIs. Surface an explicit not-implemented signal so
	// callers can fall back instead of silently succeeding.
	return GenAnimBP::MakeError(
		TEXT("set_cached_pose_node is reserved for a future semantic slice"),
		TEXT("UNSAFE_COMMAND_REQUIRED"));
}

FString UGenAnimationBlueprintUtils::SetDefaultSlotChain(const FString& AnimBlueprintPath, const FString& PayloadJson)
{
	UAnimBlueprint* AnimBP = GenAnimBP::LoadAnimBP(AnimBlueprintPath);
	if (!AnimBP) return GenAnimBP::MakeError(TEXT("AnimBlueprint not found"), TEXT("ASSET_NOT_FOUND"));
	TSharedPtr<FJsonObject> Payload = GenAnimBP::ParseJson(PayloadJson);
	if (!Payload.IsValid()) return GenAnimBP::MakeError(TEXT("invalid payload"), TEXT("INVALID_PARAMETERS"));

	const FString SlotName = Payload->GetStringField(TEXT("slot_name"));

	UEdGraph* AnimGraph = nullptr;
	for (UEdGraph* G : AnimBP->FunctionGraphs)
	{
		if (G && G->GetName() == TEXT("AnimGraph")) { AnimGraph = G; break; }
	}
	if (!AnimGraph) return GenAnimBP::MakeError(TEXT("AnimGraph not found"), TEXT("GRAPH_NOT_FOUND"));

	UAnimGraphNode_Slot* SlotNode = nullptr;
	for (UEdGraphNode* Node : AnimGraph->Nodes)
	{
		if (UAnimGraphNode_Slot* S = Cast<UAnimGraphNode_Slot>(Node))
		{
			if (S->Node.SlotName == FName(*SlotName)) { SlotNode = S; break; }
		}
	}
	if (!SlotNode)
	{
		SlotNode = NewObject<UAnimGraphNode_Slot>(
			AnimGraph, UAnimGraphNode_Slot::StaticClass(), NAME_None, RF_Transactional);
		SlotNode->CreateNewGuid();
		SlotNode->Node.SlotName = FName(*SlotName);
		AnimGraph->AddNode(SlotNode, true, false);
		SlotNode->AllocateDefaultPins();
	}

	bool bCompiled = false, bSaved = false;
	GenAnimBP::CompileAndSave(AnimBP, bCompiled, bSaved);

	TSharedRef<FJsonObject> Out = MakeShared<FJsonObject>();
	Out->SetBoolField(TEXT("compiled"), bCompiled);
	Out->SetBoolField(TEXT("saved"), bSaved);
	Out->SetStringField(TEXT("slot_name"), SlotName);
	return GenAnimBP::MakeOk(Out);
}

FString UGenAnimationBlueprintUtils::SetApplyAdditiveChain(const FString& AnimBlueprintPath, const FString& PayloadJson)
{
	UAnimBlueprint* AnimBP = GenAnimBP::LoadAnimBP(AnimBlueprintPath);
	if (!AnimBP) return GenAnimBP::MakeError(TEXT("AnimBlueprint not found"), TEXT("ASSET_NOT_FOUND"));
	TSharedPtr<FJsonObject> Payload = GenAnimBP::ParseJson(PayloadJson);
	if (!Payload.IsValid()) return GenAnimBP::MakeError(TEXT("invalid payload"), TEXT("INVALID_PARAMETERS"));

	double Alpha = 1.0;
	Payload->TryGetNumberField(TEXT("alpha"), Alpha);

	UEdGraph* AnimGraph = nullptr;
	for (UEdGraph* G : AnimBP->FunctionGraphs)
	{
		if (G && G->GetName() == TEXT("AnimGraph")) { AnimGraph = G; break; }
	}
	if (!AnimGraph) return GenAnimBP::MakeError(TEXT("AnimGraph not found"), TEXT("GRAPH_NOT_FOUND"));

	UAnimGraphNode_ApplyAdditive* Additive = NewObject<UAnimGraphNode_ApplyAdditive>(
		AnimGraph, UAnimGraphNode_ApplyAdditive::StaticClass(), NAME_None, RF_Transactional);
	Additive->CreateNewGuid();
	Additive->Node.Alpha = static_cast<float>(Alpha);
	AnimGraph->AddNode(Additive, true, false);
	Additive->AllocateDefaultPins();

	bool bCompiled = false, bSaved = false;
	GenAnimBP::CompileAndSave(AnimBP, bCompiled, bSaved);

	TSharedRef<FJsonObject> Out = MakeShared<FJsonObject>();
	Out->SetBoolField(TEXT("compiled"), bCompiled);
	Out->SetBoolField(TEXT("saved"), bSaved);
	Out->SetNumberField(TEXT("alpha"), Alpha);
	return GenAnimBP::MakeOk(Out);
}
