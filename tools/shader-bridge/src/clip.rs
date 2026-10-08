//! Explicit renderer coordinate adapter, applied after source shader arithmetic.
//!
//! Source GLES produces -w <= z <= w. The target uses reverse zero-to-one Z,
//! so the sole new operation on the completed clip output is z'=(w-z)/2.
//! Source varyings, including values computed from the unadapted clip position,
//! are not rewritten. The original module and same-API roundtrip stay intact.
use naga::{Binding, Block, BuiltIn, Expression, Handle, Literal, Module, ScalarKind,
    ShaderStage, Span, Statement, Type, TypeInner, VectorSize};

fn is_position(binding: &Option<Binding>) -> bool {
    matches!(binding, Some(Binding::BuiltIn(BuiltIn::Position { .. })))
}

pub fn reverse_zero_to_one(source: &Module) -> Result<(Module, usize), String> {
    let mut target = source.clone();
    if target.entry_points.len() != 1 || target.entry_points[0].stage != ShaderStage::Vertex {
        return Err("clip adapter requires exactly one vertex entry point".into());
    }
    let entry = &target.entry_points[0];
    let result = entry.function.result.as_ref().ok_or("vertex output is absent")?;
    let output_type = result.ty;
    let (position_index, position_type, member_count) = if is_position(&result.binding) {
        (None, output_type, 0)
    } else if let TypeInner::Struct { members, .. } = &target.types[output_type].inner {
        let positions: Vec<_> = members.iter().enumerate().filter(|(_, m)| is_position(&m.binding)).collect();
        if positions.len() != 1 {
            return Err("vertex result does not declare exactly one clip position".into());
        }
        (Some(positions[0].0 as u32), positions[0].1.ty, members.len())
    } else {
        return Err("vertex output shape is not an interface struct or clip position".into());
    };
    if !matches!(target.types[position_type].inner, TypeInner::Vector { size: VectorSize::Quad, scalar: naga::Scalar { kind: ScalarKind::Float, width: 4 } }) {
        return Err("clip position is not a four-component f32 vector".into());
    }
    let function = &mut target.entry_points[0].function;
    // Literal expressions are always in scope and must not be put in Emit.
    let half = function.expressions.append(Expression::Literal(Literal::F32(0.5)), Span::UNDEFINED);
    let mut adapter = Adapter {
        expressions: &mut function.expressions, half, output_type, position_type,
        position_index, member_count, returns: 0,
    };
    adapter.block(&mut function.body)?;
    if adapter.returns == 0 { return Err("vertex entry point has no clip output return".into()); }
    let returns = adapter.returns;
    Ok((target, returns))
}

struct Adapter<'a> {
    expressions: &'a mut naga::Arena<Expression>,
    half: Handle<Expression>,
    output_type: Handle<Type>,
    position_type: Handle<Type>,
    position_index: Option<u32>,
    member_count: usize,
    returns: usize,
}
impl Adapter<'_> {
    fn add(&mut self, expression: Expression) -> Handle<Expression> {
        self.expressions.append(expression, Span::UNDEFINED)
    }
    fn block(&mut self, block: &mut Block) -> Result<(), String> {
        let previous = std::mem::take(block);
        for (mut statement, span) in previous.span_into_iter() {
            match &mut statement {
                Statement::Block(nested) => self.block(nested)?,
                Statement::If { accept, reject, .. } => { self.block(accept)?; self.block(reject)?; }
                Statement::Switch { cases, .. } => { for case in cases { self.block(&mut case.body)?; } }
                Statement::Loop { body, continuing, .. } => { self.block(body)?; self.block(continuing)?; }
                Statement::Return { value: Some(value) } => {
                    let old = *value;
                    let first = if let Some(index) = self.position_index {
                        self.add(Expression::AccessIndex { base: old, index })
                    } else {
                        // Compose a copy so the emitted range has an ordinary
                        // expression start even when the result is a vec4.
                        self.add(Expression::Swizzle { size: VectorSize::Quad, vector: old,
                            pattern: [naga::SwizzleComponent::X,naga::SwizzleComponent::Y,
                                      naga::SwizzleComponent::Z,naga::SwizzleComponent::W] })
                    };
                    let x = self.add(Expression::AccessIndex { base: first, index: 0 });
                    let y = self.add(Expression::AccessIndex { base: first, index: 1 });
                    let z = self.add(Expression::AccessIndex { base: first, index: 2 });
                    let w = self.add(Expression::AccessIndex { base: first, index: 3 });
                    let reversed = self.add(Expression::Binary { op: naga::BinaryOperator::Subtract, left: w, right: z });
                    let normalized = self.add(Expression::Binary { op: naga::BinaryOperator::Multiply, left: reversed, right: self.half });
                    let position = self.add(Expression::Compose { ty: self.position_type, components: vec![x,y,normalized,w] });
                    let result = if let Some(position_index) = self.position_index {
                        let components = (0..self.member_count).map(|index| {
                            if index as u32 == position_index { position }
                            else { self.add(Expression::AccessIndex { base: old, index: index as u32 }) }
                        }).collect();
                        self.add(Expression::Compose { ty: self.output_type, components })
                    } else { position };
                    block.push(Statement::Emit(naga::Range::new_from_bounds(first,result)), Span::UNDEFINED);
                    *value = result;
                    self.returns += 1;
                }
                Statement::Return { value: None } => return Err("vertex entry returns without a clip output".into()),
                _ => {}
            }
            block.push(statement, span);
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn verify(text: &str, returns: usize) {
        let source = naga::front::wgsl::parse_str(text).unwrap();
        let mut validator=naga::valid::Validator::new(naga::valid::ValidationFlags::all(),naga::valid::Capabilities::all());
        validator.validate(&source).unwrap();
        let (target,count)=reverse_zero_to_one(&source).unwrap();assert_eq!(count,returns);
        let info=validator.validate(&target).unwrap();
        let generated=naga::back::wgsl::write_string(&target,&info,naga::back::wgsl::WriterFlags::EXPLICIT_TYPES).unwrap();
        let roundtrip=naga::front::wgsl::parse_str(&generated).unwrap();
        validator.validate(&roundtrip).unwrap();
    }
    #[test]
    fn direct_position_and_early_return_are_adapted() {
        verify("@vertex fn main(@location(0) p:vec4<f32>)->@builtin(position) vec4<f32>{if p.x>0.0{return p;} return p+vec4<f32>(0.0,1.0,2.0,0.0);}",2);
    }
    #[test]
    fn position_is_found_by_builtin_not_name_or_member_order() {
        verify("struct Output{@location(0) colour:vec4<f32>,@builtin(position) arbitrary_name:vec4<f32>,@location(1) source_clip:vec4<f32>}; @vertex fn main(@location(0) p:vec4<f32>)->Output{return Output(vec4<f32>(0.5),p,p);}",1);
    }
    #[test]
    fn fragment_program_cannot_receive_a_vertex_adapter() {
        let source=naga::front::wgsl::parse_str("@fragment fn main()->@location(0) vec4<f32>{return vec4<f32>(1.0);}").unwrap();
        assert!(reverse_zero_to_one(&source).is_err());
    }
}
